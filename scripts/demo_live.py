"""Fire the two demo runs at the RUNNING Gateway (:8000) so the dashboard shows them LIVE.

Unlike verify_c.py (which drives the app in-process, so its SSE never reaches the standalone
server), this POSTs to the real Gateway. Open the printed run URL immediately to watch the
agent-network graph light up node-by-node as the pipeline streams.

    # servers up first (run.ps1), then:
    PYTHONPATH=. python scripts/demo_live.py
    # open the printed http://localhost:8000/runs/<id> while it runs (~60-90s each)
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

GW = "http://localhost:8000"


def _sh(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True, text=True)


def _wr(ws, rel, c):
    import os
    p = os.path.join(ws, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(c)


def _rev(ws):
    return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def _repo(base_files, head_files):
    ws = tempfile.mkdtemp(prefix="sentinel-live-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _wr(ws, "requirements.txt", "pytest\n")
    _wr(ws, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    for r, c in base_files.items(): _wr(ws, r, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base"); base = _rev(ws)
    for r, c in head_files.items(): _wr(ws, r, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "head")
    return ws, base, _rev(ws)


def _post(name, ws, base, head, to_env):
    ev = {"event_id": f"{name}-{int(time.time()*1000)}", "source": "manual",
          "repo": {"url": ws, "name": name, "default_branch": "main"},
          "change": {"base_sha": base, "head_sha": head, "branch": f"pr-{name}", "title": "change", "author": "dev"},
          "target_transition": {"from_env": ("dev" if to_env == "test" else "qa"), "to_env": to_env},
          "requested_by": "demo"}
    body = json.dumps({"event": ev, "repo_workspace": ws}).encode()
    req = urllib.request.Request(f"{GW}/api/v1/simulate", body, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["run_id"]


def _state(rid):
    return json.load(urllib.request.urlopen(f"{GW}/api/v1/runs/{rid}"))["run"]["state"]


def main():
    happy = _repo({"calc.py": "def add(a, b):\n    return a + b\n"},
                  {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"})
    insecure = _repo(
        {"calc.py": "def add(a, b):\n    return a + b\n",
         "auth/login.py": "def authenticate(u, p, c):\n    return c.execute('SELECT 1 WHERE u=?', (u,)).fetchone()\n"},
        {"calc.py": "def add(a, b):\n    return a + b\n",
         "auth/login.py": "API_KEY = \"AKIA1234567890ABCDEF\"\n\n"
                          "def authenticate(u, p, c):\n"
                          "    q = \"SELECT id FROM users WHERE name = '\" + u + \"'\"\n"
                          "    return c.execute(q).fetchone()\n"})

    r1 = _post("live-happy", *happy, "test")
    r2 = _post("live-insecure", *insecure, "staging")
    print("\n  Runs started on the live Gateway. Open NOW to watch the graph stream:\n")
    print(f"    happy    -> {GW}/runs/{r1}")
    print(f"    insecure -> {GW}/runs/{r2}")
    print(f"    compare  -> {GW}/runs/compare?a={r1}&b={r2}\n")

    for _ in range(120):
        s1, s2 = _state(r1), _state(r2)
        print(f"    happy={s1:10}  insecure={s2}", end="\r")
        if s1 in ("done", "failed") and s2 in ("done", "failed"):
            break
        time.sleep(2)
    print(f"\n  done: happy={_state(r1)}  insecure={_state(r2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
