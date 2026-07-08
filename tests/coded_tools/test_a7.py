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


def test_decision_logger_validates_and_persists(monkeypatch):
    calls = {}
    monkeypatch.setattr(dao, "insert_decision", lambda run_id, d: calls.update(run_id=run_id, d=d))
    sly = {"run_id": "r3"}
    out = DecisionLoggerTool().invoke({"decision": _decision()}, sly)
    assert out["logged"] is True and out["approval_required"] is True
    assert contracts.is_valid("decision", sly["decision"])
    assert calls["run_id"] == "r3" and calls["d"]["decision"] == "escalate"


def test_decision_logger_rejects_invalid():
    out = DecisionLoggerTool().invoke({"decision": {"decision": "promote"}}, {"run_id": "r"})
    assert out.startswith("Error:")  # missing required transition/policy_version/...


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
