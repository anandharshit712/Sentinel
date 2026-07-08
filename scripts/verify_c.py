"""C1/C2/C4 live integration (Phase 6.1): drive the REAL Neuro-SAN network through the Gateway.

Needs: Neuro-SAN server up on :8080 with registries/sentinel.hocon (see scripts/verify_b1.py)
       + Postgres reachable (DATABASE_URL). Run:

    PYTHONPATH=. python scripts/verify_c.py

Uses FastAPI TestClient against gateway.app with the real invoker (no stub) and a real temp git
repo (repo_workspace override, so no clone). Happy path dev->test => decision "promote".
Exercises: POST /simulate -> state machine -> done, GET /runs/{id} detail, SSE replay. Exit 0 = PASS.
"""
import json
import subprocess
import sys
import tempfile
import time

from fastapi.testclient import TestClient

from gateway import app as gw


def _sh(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True, text=True)


def _write(ws, rel, content):
    import os
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(content)


def _rev(ws):
    return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _happy_repo():
    ws = tempfile.mkdtemp(prefix="sentinel-c-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")
    _write(ws, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _write(ws, "calc.py", "def add(a, b):\n    return a + b\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    _write(ws, "calc.py", "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "head")
    return ws, base, _rev(ws)


def main():
    ws, base, head = _happy_repo()
    event = {"event_id": f"c-{int(time.time())}", "source": "manual",
             "repo": {"url": f"file://{ws}", "name": "c-demo", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-c",
                        "title": "add subtract", "author": "dev"},
             "target_transition": {"from_env": "dev", "to_env": "test"},
             "requested_by": "tester"}

    with TestClient(gw.app) as c:
        r = c.post("/api/v1/simulate", json={"event": event, "repo_workspace": ws})
        print("simulate:", r.status_code, r.json())
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]

        state = "received"
        for _ in range(740):  # up to ~3700s at 5s poll; network is much faster on happy path
            state = c.get(f"/api/v1/runs/{run_id}").json()["run"]["state"]
            if state in ("done", "failed"):
                break
            time.sleep(5)
        print("final state:", state)

        detail = c.get(f"/api/v1/runs/{run_id}").json()
        dec = (detail.get("decision") or {}).get("decision")
        risk = (detail.get("risk_score") or {}).get("score")
        print("decision:", dec, "risk:", risk)

        events = c.get(f"/api/v1/runs/{run_id}/events").text
        n_events = events.count("data:")
        print("sse events replayed:", n_events)

    ok = state == "done" and dec == "promote" and n_events > 0
    print("C RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
