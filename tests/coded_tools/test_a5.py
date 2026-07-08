"""A5: incident_history (DAO monkeypatched) + deploy_window (frozen-clock vectors)."""
from coded_tools.sentinel.incident_history_tool import IncidentHistoryTool
from coded_tools.sentinel.deploy_window_tool import DeployWindowTool
from db import dao

_EVENT = {"event": {"repo": {"name": "python-payments-service"},
                    "target_transition": {"from_env": "qa", "to_env": "staging"}}}


def test_incident_history_returns_7d_and_30d(monkeypatch):
    seen = []
    monkeypatch.setattr(dao, "recent_incidents",
                        lambda repo, env, days: (seen.append((repo, env, days)) or
                                                 {"count": 2 if days == 7 else 5,
                                                  "most_recent_at": "2026-07-01T00:00:00+00:00"}))
    out = IncidentHistoryTool().invoke({}, {"run_id": "r", **_EVENT})
    assert out == {"count_7d": 2, "count_30d": 5, "most_recent_at": "2026-07-01T00:00:00+00:00"}
    assert ("python-payments-service", "staging", 7) in seen


def test_incident_history_missing_env_errors():
    assert IncidentHistoryTool().invoke({}, {"run_id": "r"}).startswith("Error:")


def _win(now):
    return DeployWindowTool().invoke({"now": now}, {"run_id": "r", **_EVENT})


def test_deploy_window_saturday_is_risky():
    assert _win("2026-07-11T10:00:00+00:00")["risky"] is True   # Sat


def test_deploy_window_friday_evening_is_risky():
    assert _win("2026-07-10T18:00:00+00:00")["risky"] is True   # Fri 16:00-23:59


def test_deploy_window_friday_morning_is_safe():
    assert _win("2026-07-10T10:00:00+00:00")["risky"] is False  # before the Fri window


def test_deploy_window_tuesday_is_safe():
    assert _win("2026-07-07T10:00:00+00:00")["risky"] is False  # Tue


def test_deploy_window_freeze_date(tmp_path):
    cfg = tmp_path / "repo_config.yaml"
    cfg.write_text(
        "repos:\n  python-payments-service:\n    timezone: UTC\n"
        "    risky_windows: []\n    freeze_dates: ['2026-07-07']\n")
    out = DeployWindowTool(repo_config_path=str(cfg)).invoke(
        {"now": "2026-07-07T10:00:00+00:00"}, {"run_id": "r", **_EVENT})
    assert out["risky"] is True and "freeze" in out["reason"]
