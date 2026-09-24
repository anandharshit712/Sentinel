"""P4 slice smoke test (10 §5): outcomes in, calibration out, recommendations that stay advice.

    PYTHONPATH=. python scripts/verify_p4.py

No LLM and no network: this phase is arithmetic over rows the system already stores, which is the
whole reason it can be verified deterministically. It seeds a synthetic history with a KNOWN shape
under its own repo name, checks the statistics and the recommendation that must follow, and then
proves the two things that would make the loop dangerous are absent:

  * a rate is never reported below the sample floor
  * nothing in the loop writes configuration

Seeded rows are left in place (they are real outcome history for a fake repo); every run uses a
fresh set of run ids so repeats do not double-count.
"""
import sys
import uuid

from db import dao
from lib import calibration

REPO = "p4-calibration-fixture"


def _seed() -> dict:
    """A history with a deliberate shape: a too-strict band and a too-loose one."""
    ids = {"promoted": [], "gated": []}
    event = {"event_id": "", "source": "manual",
             "repo": {"url": "file://x", "name": REPO, "default_branch": "main"},
             "change": {"base_sha": "0" * 40, "head_sha": "1" * 40, "branch": "b",
                        "title": "t", "author": "dev"},
             "target_transition": {"from_env": "qa", "to_env": "staging"},
             "requested_by": "verify_p4"}

    # 10 promotions at medium, 3 of which went bad -> 30%, above the "too loose" line
    for i in range(10):
        rid = str(uuid.uuid4())
        dao.ensure_run(rid, {**event, "event_id": f"p4-promo-{i}"})
        dao.save_run_payload("risk_scores", rid, {"score": 40, "band": "medium",
                                                  "formula_version": "risk-v1"},
                             score=40, band="medium", formula_version="risk-v1")
        dao.insert_decision(rid, {"decision": "promote", "policy_version": "v1",
                                  "rule_fired": "qa->staging/medium", "reasoning_trail": {},
                                  "approval_required": False})
        dao.record_outcome(rid, "reverted" if i < 3 else "clean", {"seeded_by": "verify_p4"})
        ids["promoted"].append(rid)

    # 10 escalations at high, 9 approved unchanged -> 90%, above the "too strict" line
    for i in range(10):
        rid = str(uuid.uuid4())
        dao.ensure_run(rid, {**event, "event_id": f"p4-esc-{i}"})
        dao.save_run_payload("risk_scores", rid, {"score": 70, "band": "high",
                                                  "formula_version": "risk-v1"},
                             score=70, band="high", formula_version="risk-v1")
        dao.insert_decision(rid, {"decision": "escalate", "policy_version": "v1",
                                  "rule_fired": "qa->staging/high", "reasoning_trail": {},
                                  "approval_required": True})
        with dao.get_engine().begin() as c:
            from db import models
            c.execute(models.approvals.insert().values(
                run_id=rid, status="approved" if i < 9 else "rejected", approver="verify_p4"))
        ids["gated"].append(rid)
    return ids


def main() -> int:
    ids = _seed()
    seeded = set(ids["promoted"] + ids["gated"])

    decisions = [d for d in dao.list_decisions_with_band() if str(d["run_id"]) in seeded]
    outcomes = [o for o in dao.list_outcomes() if str(o["run_id"]) in seeded]
    approvals = [a for a in dao.list_all_approvals() if str(a["run_id"]) in seeded]
    report = calibration.report(decisions, outcomes, approvals, [])

    ok, notes = True, []

    def check(cond, label, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        notes.append(f"{'ok ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")

    promo = report["promotion_by_band"].get("medium", {})
    check(promo.get("promoted") == 10 and promo.get("bad") == 3,
          "promotions counted", f"{promo.get('promoted')} promoted, {promo.get('bad')} bad")
    check(promo.get("bad_rate", {}).get("rate") == 0.3, "bad rate computed",
          str(promo.get("bad_rate", {}).get("rate")))

    esc = report["escalation_by_band"].get("high", {})
    check(esc.get("approved") == 9 and esc.get("rejected") == 1,
          "human verdicts counted", f"{esc.get('approved')} approved, {esc.get('rejected')} rejected")
    check(esc.get("approval_rate", {}).get("rate") == 0.9, "approval rate computed",
          str(esc.get("approval_rate", {}).get("rate")))

    kinds = {r["kind"]: r for r in report["recommendations"]}
    check("possibly_too_loose" in kinds, "too-loose band flagged")
    check("possibly_too_strict" in kinds, "too-strict band flagged")
    check(all(not r["applied"] for r in report["recommendations"]),
          "no recommendation was applied")
    check(report["applies_changes"] is False, "report declares it changes nothing")
    check(all(r.get("caveat") for r in report["recommendations"]),
          "every recommendation carries its caveat")

    # the floor: three observations must never produce a rate
    thin = calibration.promotion_calibration(
        [{"run_id": f"t{i}", "decision": "promote", "band": "low"} for i in range(3)],
        [{"run_id": "t0", "outcome_type": "incident"}])
    check(thin["low"]["bad_rate"]["insufficient_data"] is True,
          "thin data yields no rate", f"n={thin['low']['bad_rate']['n']}")
    check(calibration.report([{"run_id": "x", "decision": "promote", "band": "low"}],
                             [], [], [])["recommendations"] == [],
          "thin data yields no recommendation")

    # a fresh install must render
    empty = calibration.report([], [], [], [])
    check(empty["totals"]["decisions"] == 0 and empty["recommendations"] == [],
          "empty history is safe")

    for n in notes:
        print(f"    {n}")
    print(f"\nP4 RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
