"""B2 slice smoke test (07 §6, M2): a golden diff with planted security issues → ReviewReport.

Integration check — needs the neuro-san server running with registries/sentinel.hocon and NIM.
Run (see scripts/verify_b1.py for the server command), then:

    PYTHONPATH=. python scripts/verify_b2.py

Plants a hardcoded AWS key (tool-detected → Critical) and string-concatenated SQL in the auth
module (LLM-detected → Critical) in the head commit, drives the network, and asserts the ReviewReport
has a critical finding and recommendation request_changes. Exit 0 = PASS.
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


def _repo():
    ws = tempfile.mkdtemp(prefix="sentinel-b2-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "app/__init__.py", "")
    _write(ws, "app/auth/__init__.py", "")
    _write(ws, "app/auth/login.py",
           "def authenticate(user, pw, conn):\n"
           "    return conn.execute('SELECT id FROM users WHERE name = ?', (user,)).fetchone()\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    # head: SQL injection (string-concat in an auth file) + a hardcoded AWS key
    _write(ws, "app/auth/login.py",
           "API_KEY = \"AKIA1234567890ABCDEF\"\n\n"
           "def authenticate(user, pw, conn):\n"
           "    query = \"SELECT id FROM users WHERE name = '\" + user + \"' AND pw = '\" + pw + \"'\"\n"
           "    return conn.execute(query).fetchone()\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "insecure login")
    return ws, base, _rev(ws)


def main(host="localhost", port=8080):
    ws, base, head = _repo()
    event = {"event_id": "e2", "source": "manual",
             "repo": {"url": "file://x", "name": "python-payments-service", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-2",
                        "title": "insecure login", "author": "dev"},
             "target_transition": {"from_env": "qa", "to_env": "staging"},
             "requested_by": "tester"}
    sly = {"run_id": "b2-run", "event": event, "repo_workspace": ws}

    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))

    returned = mp.get_sly_data() or {}
    rr = returned.get("review_report")
    print("frontman structure:", json.dumps(mp.get_structure()))
    if not rr:
        print("B2 FAIL: no review_report; sly_data keys=", list(returned))
        return 1
    print("counts:", rr.get("counts"), "| recommendation:", rr.get("recommendation"),
          "| health:", rr.get("pr_health_score"))
    print("findings:", [(f.get("severity"), f.get("category"), f.get("file")) for f in rr.get("findings", [])])
    crit = (rr.get("counts") or {}).get("critical", 0)
    ok = crit >= 1 and rr.get("recommendation") == "request_changes"
    print("B2 RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
