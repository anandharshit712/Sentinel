"""C/6.1 live integration: drive the REAL network through the Gateway, both demo runs + approval.

Needs: Neuro-SAN server up on :8080 with registries/sentinel.hocon (see scripts/verify_b1.py)
       + Postgres reachable (DATABASE_URL). Run:

    PYTHONPATH=. python scripts/verify_c.py

FastAPI TestClient against gateway.app with the real invoker (no stub).
- Run 1 (happy, dev->test): repo_workspace override (no clone) => decision "promote".
- Run 2 (insecure, qa->staging): real git CLONE of a local repo (no override), hardcoded secret
  + SQL injection => "escalate" + approval_required; then resolve the approval (approve) via the
  Gateway and confirm it goes to "approved". Exercises simulate -> state machine -> SSE -> detail
  -> approvals queue -> resolve. Exit 0 = PASS. Prints a /runs/compare URL for the demo.
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


def _repo(files_base, files_head):
    ws = tempfile.mkdtemp(prefix="sentinel-c-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")
    _write(ws, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    for rel, c in files_base.items():
        _write(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    for rel, c in files_head.items():
        _write(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "head")
    return ws, base, _rev(ws)


def _event(name, ws, base, head, to_env):
    return {"event_id": f"{name}-{int(time.time()*1000)}", "source": "manual",
            "repo": {"url": ws, "name": name, "default_branch": "main"},
            "change": {"base_sha": base, "head_sha": head, "branch": f"pr-{name}",
                       "title": "change", "author": "dev"},
            "target_transition": {"from_env": ("dev" if to_env == "test" else "qa"), "to_env": to_env},
            "requested_by": "tester"}


def _wait_done(c, run_id, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/api/v1/runs/{run_id}").json()["run"]["state"]
        if st in ("done", "failed"):
            return st
        time.sleep(3)
    return "timeout"


def main():
    ok = True
    with TestClient(gw.app) as c:
        # Run 1 — happy path, repo_workspace override (no clone) -> promote
        ws1, b1, h1 = _repo(
            {"calc.py": "def add(a, b):\n    return a + b\n"},
            {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"})
        ev1 = _event("c-happy", ws1, b1, h1, "test")
        r1 = c.post("/api/v1/simulate", json={"event": ev1, "repo_workspace": ws1})
        assert r1.status_code == 202, r1.text
        rid1 = r1.json()["run_id"]
        s1 = _wait_done(c, rid1)
        d1 = c.get(f"/api/v1/runs/{rid1}").json()
        dec1 = (d1.get("decision") or {}).get("decision")
        print(f"[happy]   state={s1} decision={dec1} risk={(d1.get('risk_score') or {}).get('score')}")
        ok = ok and s1 == "done" and dec1 == "promote"

        # Run 2 — insecure, REAL CLONE (no override), secret + SQLi, qa->staging -> escalate
        ws2, b2, h2 = _repo(
            {"calc.py": "def add(a, b):\n    return a + b\n",
             "auth/login.py": "def authenticate(u, p, c):\n    return c.execute('SELECT 1 WHERE u=?', (u,)).fetchone()\n"},
            {"calc.py": "def add(a, b):\n    return a + b\n",
             "auth/login.py": "API_KEY = \"AKIA1234567890ABCDEF\"\n\n"
                              "def authenticate(u, p, c):\n"
                              "    q = \"SELECT id FROM users WHERE name = '\" + u + \"'\"\n"
                              "    return c.execute(q).fetchone()\n"})
        ev2 = _event("c-insecure", ws2, b2, h2, "staging")
        r2 = c.post("/api/v1/simulate", json={"event": ev2})  # no repo_workspace -> Gateway clones
        assert r2.status_code == 202, r2.text
        rid2 = r2.json()["run_id"]
        s2 = _wait_done(c, rid2)
        d2 = c.get(f"/api/v1/runs/{rid2}").json()
        dec2 = (d2.get("decision") or {}).get("decision")
        risk2 = (d2.get("risk_score") or {}).get("score")
        crit = ((d2.get("review_report") or {}).get("counts") or {}).get("critical")
        print(f"[insecure] state={s2} decision={dec2} risk={risk2} criticals={crit}")
        ok = ok and s2 == "done" and dec2 == "escalate"

        # Resolve the escalation through the Gateway (approve)
        pend = c.get("/api/v1/approvals?status=pending").json()["approvals"]
        appr = next((a for a in pend if a["run_id"] == rid2), None)
        if appr:
            res = c.post(f"/api/v1/approvals/{appr['id']}", json={"action": "approve", "comment": "reviewed"})
            print(f"[approval] resolved id={appr['id']} -> {res.json().get('status')}")
            ok = ok and res.json().get("status") == "approved"
        else:
            print("[approval] FAIL: no pending approval for insecure run")
            ok = False

        # SSE replay sanity on both
        n1 = c.get(f"/api/v1/runs/{rid1}/events").text.count("data:")
        n2 = c.get(f"/api/v1/runs/{rid2}/events").text.count("data:")
        print(f"[sse] happy={n1} insecure={n2} events")
        ok = ok and n1 > 0 and n2 > 0

        print(f"\nDemo compare URL:  /runs/compare?a={rid1}&b={rid2}")

    print("C/6.1 RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
