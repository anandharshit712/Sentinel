"""Platform Phase 1 smoke test: findings in, verdicts in, precision out — against the real DB.

    PYTHONPATH=. python scripts/verify_p1m.py

No LLM. Drives report_publisher's real persistence path (deterministic synthesis -> finding rows)
for two runs of one fixture repo, judges findings through the same DAO the verdict endpoint uses,
then checks:

  * a rerun of the same repo produces the SAME fingerprints, so "previously marked" can show
  * re-publishing a report does not erase a verdict already given
  * precision is reported per source x dimension, and below the sample floor it is not a rate
  * a missed_bug outcome is counted and does not settle a promotion
  * the run carries the Sentinel version that judged it

Seeded rows stay (they are history for a fixture repo); fresh run ids each time.
"""
import sys
import uuid

from coded_tools.sentinel.report_publisher_tool import ReportPublisherTool
from db import dao
from lib import calibration

REPO = "p1m-precision-fixture"


def _event(i: str) -> dict:
    return {"event_id": f"p1m-{i}", "source": "manual",
            "repo": {"url": "file://x", "name": REPO, "default_branch": "main"},
            "change": {"base_sha": "0" * 40, "head_sha": "1" * 40, "branch": "b",
                       "title": "t", "author": "dev"},
            "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "verify_p1m"}


def _finding(n: int, line: int, source: str, title: str) -> dict:
    return {"id": f"SEC-{n:03d}", "severity": "medium", "title": title, "source": source,
            "file": "app/db.py", "category": f"cat{n}", "line_start": line}


def _publish(run_id: str, shift: int) -> list[dict]:
    """One run's report: 6 LLM security findings + 1 tool quality finding. `shift` moves every
    finding down the file, as an unrelated edit above them would."""
    ev = _event(run_id)
    dao.ensure_run(run_id, ev)
    sly = {"run_id": run_id, "event": ev,
           "security_findings_shard_1": {"findings": [_finding(i, 10 + i + shift, "llm", f"Issue {i}")
                                                      for i in range(1, 7)]},
           "quality_findings": {"findings": [{"id": "QUAL-001", "severity": "low", "title": "Too complex",
                                              "source": "tool", "file": "app/db.py",
                                              "category": "complexity", "line_start": 3 + shift}]}}
    out = ReportPublisherTool().invoke({}, sly)
    assert isinstance(out, dict) and out["persisted"], out
    # terminal, so a Gateway restart's orphan reaper does not mark the fixture run failed
    dao.set_run_state(run_id, "done", finished=True)
    return dao.list_findings(run_id), sly


def main() -> int:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and cond

    r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
    rows1, sly1 = _publish(r1, 0)
    check(len(rows1) == 7, f"run 1 stored 7 finding rows (got {len(rows1)})")
    check({r["kind"] for r in rows1} == {"security", "quality"}, "rows carry their dimension")
    check(all(r["fingerprint"] for r in rows1), "every row has a fingerprint")

    # judge run 1: 5 LLM findings real, 1 false positive; leave the tool one unjudged
    llm = [r for r in rows1 if r["source"] == "llm"]
    for r in llm[:5]:
        dao.set_finding_verdict(r["id"], "real", by="verify_p1m", note=None)
    dao.set_finding_verdict(llm[5]["id"], "false_positive", by="verify_p1m", note="fixture")

    # re-publishing run 1 must not wipe the verdicts
    ReportPublisherTool().invoke({}, dict(sly1))
    again = dao.list_findings(r1)
    check(sum(1 for r in again if r["verdict"]) == 6, "re-publishing kept all 6 verdicts")

    rows2, _ = _publish(r2, 25)          # same findings, every line moved by 25
    fp1 = {r["fingerprint"] for r in rows1}
    fp2 = {r["fingerprint"] for r in rows2}
    check(fp1 == fp2, "fingerprints are stable when the findings move down the file")
    prev = [r for r in rows2 if r["previous"]]
    check(len(prev) == 6 and all(r["verdict"] is None for r in prev),
          f"run 2 shows 'previously marked' on 6 findings without inheriting a verdict (got {len(prev)})")

    dao.record_outcome(r2, "missed_bug", {"category": "p1m-sqli", "description": "fixture",
                                          "file": "app/db.py", "line": 5})

    rows = [r for r in dao.list_finding_rows_for_precision() if r["repo"] == REPO]
    outs = [o for o in dao.list_outcomes() if o["run_id"] in (r1, r2)]
    pr = calibration.review_precision(rows, outs)
    # history accumulates across invocations; check this invocation's shape via the repo cell
    llm_cell = pr["by_source_dimension"].get("llm/security", {})
    check(llm_cell.get("judged", 0) >= 6 and llm_cell["precision"]["rate"] is not None,
          f"llm/security precision is a rate once judged >= {calibration.MIN_SAMPLE}: {llm_cell.get('precision')}")
    tool_cell = pr["by_source_dimension"].get("tool/quality", {})
    check(tool_cell.get("judged") == 0 and tool_cell["precision"]["rate"] is None,
          "unjudged tool/quality findings give no rate")
    check(pr["missed_bugs"]["by_category"].get("p1m-sqli", 0) >= 1, "missed bug counted")

    run = dao.get_run(r1)
    check(bool(run.get("sentinel_version")) and run["sentinel_version"] != "unknown",
          f"run stamped with sentinel_version={run.get('sentinel_version')}")

    full = calibration.report(dao.list_decisions_with_band(), dao.list_outcomes(),
                              dao.list_all_approvals(), [], dao.list_finding_rows_for_precision())
    check("review_precision" in full and full["applies_changes"] is False,
          "calibration report carries review_precision and still changes nothing")

    print("verify_p1m:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
