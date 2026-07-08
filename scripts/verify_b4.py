"""B4 slice smoke test (07 §6, M3): full pipeline → risk + promotion decision, both demo runs.

Needs the server running with registries/sentinel.hocon and NIM (see scripts/verify_b1.py). Run:

    PYTHONPATH=. python scripts/verify_b4.py

Run 1 (happy): a benign change on dev->test → expect decision "promote".
Run 2 (insecure): hardcoded secret + SQL injection on qa->staging → expect "escalate" with a
                  reasoning trail mentioning the risk/policy. Exit 0 = both PASS.
Repo names are intentionally not in repo_config so no smoke id points at a non-existent test.
"""
import json
import subprocess
import sys
import tempfile

from neuro_san.client.agent_session_factory import AgentSessionFactory
from neuro_san.client.streaming_input_processor import StreamingInputProcessor


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


def _commit_pair(files_base, files_head):
    ws = tempfile.mkdtemp(prefix="sentinel-b4-")
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


def _run(ws, base, head, to_env, host, port):
    event = {"event_id": "e4", "source": "manual",
             "repo": {"url": "file://x", "name": "b4-demo", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-4",
                        "title": "change", "author": "dev"},
             "target_transition": {"from_env": ("dev" if to_env == "test" else "qa"), "to_env": to_env},
             "requested_by": "tester"}
    sly = {"run_id": f"b4-{to_env}", "event": event, "repo_workspace": ws}
    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))
    return mp.get_structure(), (mp.get_sly_data() or {})


def main(host="localhost", port=8080):
    ok = True

    # Run 1 — happy path (benign change, dev->test) -> promote
    ws, base, head = _commit_pair(
        {"calc.py": "def add(a, b):\n    return a + b\n"},
        {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"})
    struct, sd = _run(ws, base, head, "test", host, port)
    dec = (sd.get("decision") or {}).get("decision")
    risk = (sd.get("risk_score") or {}).get("score")
    print(f"[happy] structure={json.dumps(struct)}")
    print(f"[happy] risk={risk} decision={dec}")
    ok = ok and dec == "promote"
    print("[happy]", "PASS" if dec == "promote" else "FAIL")

    # Run 2 — insecure (hardcoded secret + SQLi in auth, qa->staging) -> escalate
    ws, base, head = _commit_pair(
        {"calc.py": "def add(a, b):\n    return a + b\n",
         "auth/login.py": "def authenticate(u, p, c):\n    return c.execute('SELECT 1 WHERE u=?', (u,)).fetchone()\n"},
        {"calc.py": "def add(a, b):\n    return a + b\n",
         "auth/login.py": "API_KEY = \"AKIA1234567890ABCDEF\"\n\n"
                          "def authenticate(u, p, c):\n"
                          "    q = \"SELECT id FROM users WHERE name = '\" + u + \"'\"\n"
                          "    return c.execute(q).fetchone()\n"})
    struct, sd = _run(ws, base, head, "staging", host, port)
    decision = sd.get("decision") or {}
    dec = decision.get("decision")
    risk = (sd.get("risk_score") or {}).get("score")
    trail = decision.get("reasoning_trail") or {}
    print(f"[insecure] structure={json.dumps(struct)}")
    print(f"[insecure] risk={risk} decision={dec} trail.policy={trail.get('policy')}")
    ok = ok and dec == "escalate"
    print("[insecure]", "PASS" if dec == "escalate" else "FAIL")

    print("B4 RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
