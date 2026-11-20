"""Platform Phase 1: review findings become rows a human can judge, and precision becomes a number.

The failure modes worth guarding: a fingerprint that changes when unrelated code moves (so a
verdict never carries over), two shard findings collapsing into one row, a precision figure
computed from three verdicts, and an unjudged finding silently counted as correct.
"""
import pytest
from fastapi.testclient import TestClient

from coded_tools.sentinel import report_publisher_tool as rp
from db import dao
from gateway import app as gw
from lib import calibration


def _f(**kw):
    base = {"id": "SEC-001", "severity": "high", "title": "SQL injection in get_user",
            "source": "llm", "file": "app/db.py", "category": "owasp:sqli", "line_start": 12}
    return {**base, **kw}


# ---------------------------------------------------------------- fingerprint
def test_fingerprint_survives_the_finding_moving_down_the_file():
    a = rp.fingerprint("repo", _f(line_start=12, id="SEC-001"))
    b = rp.fingerprint("repo", _f(line_start=40, id="SEC-007"))
    assert a == b


def test_fingerprint_ignores_numbers_in_the_title():
    a = rp.fingerprint("repo", _f(title="Function has 14 branches"))
    b = rp.fingerprint("repo", _f(title="Function has 15 branches"))
    assert a == b


def test_fingerprint_separates_repos_files_and_categories():
    base = rp.fingerprint("repo", _f())
    assert rp.fingerprint("other", _f()) != base
    assert rp.fingerprint("repo", _f(file="app/other.py")) != base
    assert rp.fingerprint("repo", _f(category="owasp:xss")) != base


# ---------------------------------------------------------------- merge -> rows
def test_shard_ids_that_collide_get_distinct_row_keys():
    fs = [_f(id="SEC-001", line_start=1), _f(id="SEC-001", line_start=2), _f(id="SEC-001", line_start=3)]
    rp._unique_ids(fs)
    assert [f["id"] for f in fs] == ["SEC-001", "SEC-001#2", "SEC-001#3"]


def test_synthesis_tags_every_finding_with_its_dimension_and_fingerprint():
    sly = {"run_id": "r", "event": {"repo": {"name": "demo"}},
           "security_findings_shard_1": {"findings": [_f(id="SEC-001")]},
           "security_findings_shard_2": {"findings": [_f(id="SEC-001", line_start=99,
                                                         title="Hardcoded key")]},
           "quality_findings": {"findings": [_f(id="QUAL-001", category="complexity",
                                                severity="low", title="Too complex")]},
           "performance_findings": {"findings": [_f(id="PERF-001", category="n_plus_one",
                                                    severity="medium", title="N+1")]}}
    report = rp._synthesize(sly)
    dims = {f["title"]: f["dimension"] for f in report["findings"]}
    assert dims == {"SQL injection in get_user": "security", "Hardcoded key": "security",
                    "Too complex": "quality", "N+1": "performance"}
    ids = [f["id"] for f in report["findings"]]
    assert len(ids) == len(set(ids)), ids
    assert all(f["fingerprint"] == rp.fingerprint("demo", f) for f in report["findings"])

    rows = rp._finding_rows("demo", report["findings"])
    assert {r["kind"] for r in rows} == {"security", "quality", "performance"}
    assert all(r["finding_id"] and r["fingerprint"] and r["payload"] for r in rows)


def test_report_publisher_writes_finding_rows_and_survives_their_failure(monkeypatch):
    saved = {}
    monkeypatch.setattr(dao, "save_run_payload", lambda *a, **k: None)
    monkeypatch.setattr(dao, "save_findings", lambda run_id, rows: saved.update(run=run_id, rows=rows))
    sly = {"run_id": "r1", "event": {"repo": {"name": "demo"}},
           "security_findings": {"findings": [_f()]}}
    out = rp.ReportPublisherTool().invoke({}, sly)
    assert out["persisted"] is True and saved["run"] == "r1" and len(saved["rows"]) == 1

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(dao, "save_findings", boom)
    out = rp.ReportPublisherTool().invoke({}, dict(sly))
    # the report still comes back with its real numbers; the failure is named, not swallowed
    assert out["persisted"] is False and "findings: db down" in out["persist_error"]
    assert out["counts"]["high"] == 1


# ---------------------------------------------------------------- precision
def _row(source="llm", kind="security", verdict=None, repo="r", version="v1"):
    return {"source": source, "kind": kind, "verdict": verdict, "repo": repo,
            "sentinel_version": version}


def test_precision_below_the_sample_floor_is_not_a_rate():
    rows = [_row(verdict="real")] * 4
    p = calibration.review_precision(rows)["by_source_dimension"]["llm/security"]
    assert p["precision"] == {"n": 4, "rate": None, "insufficient_data": True}


def test_unjudged_findings_are_reported_but_never_counted_as_correct():
    rows = [_row(verdict="false_positive")] * 5 + [_row()] * 20
    p = calibration.review_precision(rows)["by_source_dimension"]["llm/security"]
    assert p["reported"] == 25 and p["judged"] == 5
    assert p["precision"]["rate"] == 0.0


def test_wont_fix_counts_as_a_true_finding():
    rows = [_row(verdict="wont_fix")] * 3 + [_row(verdict="real")] * 2
    p = calibration.review_precision(rows)["by_source_dimension"]["llm/security"]
    assert p["precision"]["rate"] == 1.0


def test_precision_is_split_by_version():
    rows = [_row(verdict="real", version="a")] * 5 + [_row(verdict="false_positive", version="b")] * 5
    v = calibration.review_precision(rows)["by_version"]
    assert v["a"]["precision"]["rate"] == 1.0 and v["b"]["precision"]["rate"] == 0.0


def test_missed_bugs_are_counted_and_never_settle_a_promotion():
    outcomes = [{"run_id": "x", "outcome_type": "missed_bug", "payload": {"category": "sqli"}},
                {"run_id": "y", "outcome_type": "missed_bug", "payload": {}}]
    p = calibration.review_precision([], outcomes)
    assert p["missed_bugs"] == {"total": 2, "by_category": {"sqli": 1, "unspecified": 1}}
    promo = calibration.promotion_calibration(
        [{"run_id": "x", "decision": "promote", "band": "low"}], outcomes)
    assert promo["low"]["unknown"] == 1      # still unknown, not silently "clean"


def test_report_carries_precision_and_is_safe_empty():
    r = calibration.report([], [], [], [], [])
    assert r["review_precision"]["missed_bugs"]["total"] == 0
    assert r["review_precision"]["by_source_dimension"] == {}


# ---------------------------------------------------------------- gateway
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dao, "reap_unfinished_runs", lambda: [])
    monkeypatch.setattr(gw.settings, "OPEN_MODE", True)
    with TestClient(gw.app) as c:
        yield c


def test_verdict_endpoint_validates_and_records(client, monkeypatch):
    calls = []
    monkeypatch.setattr(dao, "set_finding_verdict",
                        lambda rid, v, by, note: calls.append((rid, v, by, note))
                        or {"id": rid, "run_id": "r", "verdict": v, "fingerprint": "f"})
    assert client.post("/api/v1/findings/7/verdict", json={"verdict": "maybe"}).status_code == 400
    r = client.post("/api/v1/findings/7/verdict", json={"verdict": "false_positive", "note": " test fixture "})
    assert r.status_code == 200 and r.json()["verdict"] == "false_positive"
    assert calls == [(7, "false_positive", "admin", "test fixture")]


def test_verdict_on_unknown_finding_is_404(client, monkeypatch):
    monkeypatch.setattr(dao, "set_finding_verdict", lambda *a, **k: None)
    assert client.post("/api/v1/findings/999/verdict", json={"verdict": "real"}).status_code == 404


def test_missed_bug_needs_a_description(client, monkeypatch):
    monkeypatch.setattr(dao, "get_run", lambda rid: {"run_id": rid})
    rec = []
    monkeypatch.setattr(dao, "record_outcome", lambda rid, kind, payload: rec.append(payload) or 1)
    monkeypatch.setattr(dao, "record_audit", lambda *a, **k: None)
    assert client.post("/api/v1/runs/r/outcome", json={"outcome": "missed_bug"}).status_code == 400
    r = client.post("/api/v1/runs/r/outcome", json={"outcome": "missed_bug", "file": "a.py", "line": 3,
                                                    "category": "sqli", "description": "raw query"})
    assert r.status_code == 201
    assert rec[0]["category"] == "sqli" and rec[0]["description"] == "raw query"
