"""A7: contract_store, report_publisher, decision_logger, cicd_action, notification.

DB writes are monkeypatched so these stay logic-only (the real round-trip is covered by dao.demo).
"""
from coded_tools.sentinel.contract_store_tool import ContractStoreTool
from coded_tools.sentinel.report_publisher_tool import ReportPublisherTool
from coded_tools.sentinel.decision_logger_tool import DecisionLoggerTool
from coded_tools.sentinel.cicd_action_tool import CicdActionTool
from coded_tools.sentinel.notification_tool import NotificationTool
from db import dao
from lib import contracts


def _decision(**over):
    d = {"decision": "escalate", "transition": {"from_env": "qa", "to_env": "staging"},
         "policy_version": "v1", "rule_fired": "qa->staging/high",
         "reasoning_trail": {"policy": "high band escalates"}, "approval_required": True}
    d.update(over)
    return d


def test_contract_store_writes_valid_contract_to_sly_data():
    sly = {"run_id": "r1"}
    out = ContractStoreTool().invoke(
        {"contract_name": "security_findings", "payload": {"findings": []}}, sly)
    assert out == {"stored": "security_findings"}
    assert contracts.is_valid("security_findings", sly["security_findings"])
    assert sly["security_findings"]["produced_by"] == "agent:security_findings"


def test_contract_store_rejects_unknown_name():
    out = ContractStoreTool().invoke({"contract_name": "risk_score", "payload": {}}, {"run_id": "r"})
    assert out.startswith("Error:")  # enum-restricted; cannot overwrite tool-owned contracts


def test_contract_store_rejects_invalid_payload():
    out = ContractStoreTool().invoke(
        {"contract_name": "security_findings", "payload": {"nope": 1}}, {"run_id": "r"})
    assert out.startswith("Error: schema")


def test_report_publisher_validates_persists_and_stores(monkeypatch):
    calls = {}
    monkeypatch.setattr(dao, "save_run_payload",
                        lambda table, run_id, payload, **cols: calls.update(
                            table=table, run_id=run_id, cols=cols))
    sly = {"run_id": "r2"}
    report = {"executive_summary": "ok", "findings": [], "pr_health_score": 88,
              "recommendation": "approve"}
    out = ReportPublisherTool().invoke({"review_report": report}, sly)
    assert out["published"] is True
    assert contracts.is_valid("review_report", sly["review_report"])
    assert calls["table"] == "review_reports" and calls["run_id"] == "r2"
    assert calls["cols"] == {"pr_health_score": 88, "recommendation": "approve"}


def test_report_publisher_synthesizes_from_sly_data(monkeypatch):
    monkeypatch.setattr(dao, "save_run_payload", lambda *a, **k: None)
    sly = {"run_id": "r7",
           "security_findings": {"findings": [
               {"id": "SEC-001", "severity": "critical", "file": "a.py", "line_start": 1,
                "category": "sql_injection", "title": "SQLi", "source": "llm"},
               {"id": "SEC-002", "severity": "critical", "file": "a.py", "line_start": 1,
                "category": "sql_injection", "title": "SQLi dup", "source": "tool"}]},
           "quality_findings": {"findings": [
               {"id": "QUAL-001", "severity": "medium", "file": "b.py", "line_start": 9,
                "category": "complexity", "title": "complex fn", "source": "llm"}]}}
    out = ReportPublisherTool().invoke({}, sly)
    assert out["published"] is True
    rr = sly["review_report"]
    assert rr["counts"] == {"critical": 1, "high": 0, "medium": 1, "low": 0}  # SEC dup merged
    assert rr["pr_health_score"] == 100 - 25 - 4
    assert rr["recommendation"] == "request_changes"
    assert contracts.is_valid("review_report", rr)


def test_contract_store_accepts_shard_and_senior_names():
    for name in ["security_findings_shard_1", "security_findings_shard_4"]:
        sly = {"run_id": "r"}
        out = ContractStoreTool().invoke({"contract_name": name, "payload": {"findings": []}}, sly)
        assert out == {"stored": name} and contracts.is_valid(name, sly[name])
    sly = {"run_id": "r"}
    out = ContractStoreTool().invoke({"contract_name": "senior_summary", "payload": {"summary": "clean"}}, sly)
    assert out == {"stored": "senior_summary"}


def test_contract_store_still_rejects_tool_owned_review_plan():
    out = ContractStoreTool().invoke({"contract_name": "review_plan", "payload": {}}, {"run_id": "r"})
    assert out.startswith("Error:")  # review_plan is written by review_planner, not agents


def test_report_publisher_merges_shard_findings_and_dedups(monkeypatch):
    monkeypatch.setattr(dao, "save_run_payload", lambda *a, **k: None)
    sly = {"run_id": "rs",
           "security_findings_shard_1": {"findings": [
               {"id": "SEC1-001", "severity": "critical", "file": "a.py", "line_start": 1,
                "category": "sql_injection", "title": "SQLi", "source": "tool"}]},
           "security_findings_shard_2": {"findings": [
               {"id": "SEC1-001d", "severity": "critical", "file": "a.py", "line_start": 1,
                "category": "sql_injection", "title": "SQLi dup", "source": "llm"},  # cross-shard dup
               {"id": "SEC2-002", "severity": "high", "file": "b.py", "line_start": 5,
                "category": "xss", "title": "XSS", "source": "llm"}]}}
    out = ReportPublisherTool().invoke({}, sly)
    rr = sly["review_report"]
    assert rr["counts"] == {"critical": 1, "high": 1, "medium": 0, "low": 0}  # a.py dup merged
    assert out["published"] is True and contracts.is_valid("review_report", rr)


def test_report_publisher_adds_coverage_and_uses_senior_summary(monkeypatch):
    monkeypatch.setattr(dao, "save_run_payload", lambda *a, **k: None)
    sly = {"run_id": "rc",
           "review_plan": {"shards": [{"shard": 1, "files": ["a.py"]}, {"shard": 2, "files": ["b.py"]}],
                           "metrics": {"shard_count": 2, "added_lines": 120}},
           "review_coverage": {"1": {"snippet_lines": 10, "shard": 1}},  # shard 2 never ran
           "senior_summary": {"summary": "One critical in auth."},
           "security_findings_shard_1": {"findings": [
               {"id": "S1", "severity": "critical", "file": "a.py", "line_start": 1,
                "category": "sqli", "title": "SQLi", "source": "tool"}]}}
    ReportPublisherTool().invoke({}, sly)
    rr = sly["review_report"]
    assert rr["executive_summary"].startswith("One critical in auth.")  # senior narrative used
    assert "Coverage:" in rr["executive_summary"]
    cov = rr["coverage"]
    assert cov["total_added_lines"] == 120 and cov["llm_reviewed_lines"] == 10
    assert cov["shards"] == 2 and cov["unscanned_shards"] == [2]  # reviewer 2 didn't run
    assert contracts.is_valid("review_report", rr)


def test_report_publisher_without_plan_has_no_coverage(monkeypatch):
    monkeypatch.setattr(dao, "save_run_payload", lambda *a, **k: None)
    sly = {"run_id": "rn", "security_findings": {"findings": []}, "quality_findings": {"findings": []}}
    ReportPublisherTool().invoke({}, sly)
    rr = sly["review_report"]
    assert "coverage" not in rr and contracts.is_valid("review_report", rr)


def test_decision_logger_validates_and_persists(monkeypatch):
    calls = {}
    monkeypatch.setattr(dao, "insert_decision", lambda run_id, d: calls.update(run_id=run_id, d=d))
    sly = {"run_id": "r3"}
    out = DecisionLoggerTool().invoke({"decision": _decision()}, sly)
    assert out["logged"] is True and out["approval_required"] is True
    assert contracts.is_valid("decision", sly["decision"])
    assert calls["run_id"] == "r3" and calls["d"]["decision"] == "escalate"


def test_decision_logger_builds_from_sly_data(monkeypatch):
    calls = {}
    monkeypatch.setattr(dao, "insert_decision", lambda run_id, d: calls.update(d=d))
    sly = {"run_id": "r8",
           "ladder_verdict": {"decision": "escalate", "rule_fired": "qa->staging/critical", "policy_version": "ladder-v1"},
           "event": {"target_transition": {"from_env": "qa", "to_env": "staging"}},
           "risk_score": {"score": 80, "band": "critical"},
           "review_report": {"executive_summary": "1 critical", "counts": {"critical": 1}},
           "test_results": {"totals": {"passed": 2, "failed": 0}}}
    out = DecisionLoggerTool().invoke({}, sly)  # no decision arg -> build from sly_data
    assert out["logged"] is True and out["decision"] == "escalate" and out["approval_required"] is True
    assert contracts.is_valid("decision", sly["decision"]), contracts.iter_errors("decision", sly["decision"])
    assert calls["d"]["transition"] == {"from_env": "qa", "to_env": "staging"}


def test_decision_logger_errors_without_verdict():
    out = DecisionLoggerTool().invoke({}, {"run_id": "r"})
    assert out.startswith("Error:")  # no decision arg and no ladder_verdict


def test_cicd_action_simulate_appends_actions_taken(monkeypatch):
    monkeypatch.setenv("SIMULATE_CICD", "true")
    sly = {"run_id": "r4", "decision": {"decision": "promote", "actions_taken": []}}
    out = CicdActionTool().invoke({"action": "promote"}, sly)
    assert out["action"] == "none" and out["detail"] == "simulated"
    assert sly["decision"]["actions_taken"][-1]["action"] == "none"


def test_cicd_action_rejects_bad_action():
    assert CicdActionTool().invoke({"action": "delete"}, {"run_id": "r"}).startswith("Error:")


def test_notification_inserts_row(monkeypatch):
    calls = {}
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(dao, "insert_notification",
                        lambda run_id, kind, summary: calls.update(kind=kind, summary=summary))
    out = NotificationTool().invoke({"kind": "escalate", "summary": "risk 80"}, {"run_id": "r5"})
    assert out["notified"] is True and out["delivered"]["dashboard"] is True
    assert calls == {"kind": "escalate", "summary": "risk 80"}


def test_notification_db_failure_is_non_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(dao, "insert_notification", boom)
    out = NotificationTool().invoke({"kind": "hold", "summary": "x"}, {"run_id": "r6"})
    assert out["notified"] is True and out["delivered"]["dashboard"] is False  # logged, not raised


def test_notification_rejects_bad_kind():
    assert NotificationTool().invoke({"kind": "spam", "summary": ""}, {"run_id": "r"}).startswith("Error:")
