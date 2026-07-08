"""C1/C2/C4 gateway smoke: simulate -> state machine -> done, idempotency, SSE, approvals.

No real Neuro-SAN server and no DB: the invoker is stubbed with staged progress and the DAO is
an in-memory fake. Verifies the state machine advances off progress markers and the run reaches
`done` with the stubbed decision.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from db import dao
from gateway import app as gw


class FakeDB:
    def __init__(self):
        self.runs, self.decisions, self.approvals, self.audit = {}, {}, [], []

    def insert_run(self, run_id, event, source, repo, from_env, to_env, state="received"):
        self.runs[run_id] = {"run_id": run_id, "event": event, "source": source, "repo": repo,
                             "from_env": from_env, "to_env": to_env, "state": state,
                             "finished_at": None, "created_at": "t"}

    def set_run_state(self, run_id, state, finished=False):
        self.runs[run_id]["state"] = state
        if finished:
            self.runs[run_id]["finished_at"] = "t"

    def get_run(self, run_id):
        return dict(self.runs[run_id]) if run_id in self.runs else None

    def record_audit(self, run_id, actor, action, payload=None):
        self.audit.append({"run_id": run_id, "actor": actor, "action": action, "payload": payload})

    def find_run_by_event_id(self, event_id):
        hits = [r for r in self.runs.values() if (r["event"] or {}).get("event_id") == event_id]
        return hits[-1]["run_id"] if hits else None

    def get_decision(self, run_id):
        return self.decisions.get(run_id)

    def get_payload(self, table, run_id):
        return None

    def list_runs(self, **kw):
        return list(self.runs.values())

    def list_approvals(self, status="pending"):
        return [a for a in self.approvals if a["status"] == status]

    def list_audit(self, run_id=None, limit=200):
        return [a for a in self.audit if run_id is None or a["run_id"] == run_id]


def _fake_invoke(run_id, event, ws, *, host, port, network, on_progress=None):
    for stage in ("change_analysis_agent", "security_review_agent", "code_quality_agent",
                  "test_runner", "risk_scoring_agent", "promotion_gating_agent"):
        if on_progress:
            on_progress({"text": f"calling {stage}", "origin": [stage]})
    return {"decision": "promote"}, {"decision": {"decision": "promote"}}, "ok"


_EVENT = {"event_id": "evt-1", "source": "manual",
          "repo": {"url": "file:///x", "name": "python-payments-service"},
          "change": {"base_sha": "aaa", "head_sha": "bbb", "branch": "pr-1"},
          "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "tester"}


@pytest.fixture
def client(monkeypatch):
    fake = FakeDB()
    for name in ("insert_run", "set_run_state", "get_run", "record_audit",
                 "find_run_by_event_id", "get_decision", "get_payload",
                 "list_runs", "list_approvals", "list_audit"):
        monkeypatch.setattr(dao, name, getattr(fake, name))
    monkeypatch.setattr(gw, "invoke_network", _fake_invoke)
    # context-manager client => one persistent portal/loop so background tasks actually run
    with TestClient(gw.app) as c:
        yield c, fake


def _wait_done(c, run_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/api/v1/runs/{run_id}").json()["run"]["state"]
        if st in ("done", "failed"):
            return st
        time.sleep(0.05)
    return "timeout"


def test_simulate_runs_to_done(client):
    c, fake = client
    r = c.post("/api/v1/simulate", json={"event": _EVENT, "repo_workspace": "/tmp/ws"})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    assert _wait_done(c, run_id) == "done"
    # state machine walked forward and emitted progress
    kinds = {e["kind"] for e in gw.bus.replay(run_id)}
    assert "agent_message" in kinds and "state_change" in kinds
    states = [e["state"] for e in gw.bus.replay(run_id) if e["kind"] == "state_change"]
    assert states == sorted(states, key=gw._rank)  # monotonic
    assert states[-1] == "done"


def test_simulate_is_idempotent(client):
    c, fake = client
    r1 = c.post("/api/v1/simulate", json={"event": _EVENT, "repo_workspace": "/tmp/ws"})
    rid = r1.json()["run_id"]
    _wait_done(c, rid)
    r2 = c.post("/api/v1/simulate", json={"event": _EVENT, "repo_workspace": "/tmp/ws"})
    assert r2.json()["idempotent"] is True and r2.json()["run_id"] == rid


def test_simulate_rejects_incomplete_event(client):
    c, _ = client
    r = c.post("/api/v1/simulate", json={"event": {"event_id": "x"}, "repo_workspace": "/tmp/ws"})
    assert r.status_code == 400


def test_sse_replays_terminal(client):
    c, _ = client
    rid = c.post("/api/v1/simulate", json={"event": _EVENT, "repo_workspace": "/tmp/ws"}).json()["run_id"]
    assert _wait_done(c, rid) == "done"
    with c.stream("GET", f"/api/v1/runs/{rid}/events") as s:
        lines = [ln for ln in s.iter_lines() if ln.startswith("data:")]
    payloads = [json.loads(ln[5:].strip()) for ln in lines]
    assert any(p["kind"] == "state_change" and p.get("state") == "done" for p in payloads)


def test_reject_approval_needs_comment(client):
    c, _ = client
    r = c.post("/api/v1/approvals/1", json={"action": "reject"})
    assert r.status_code == 400
