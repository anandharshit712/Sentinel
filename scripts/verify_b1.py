"""B1 slice smoke test (07 §6): a golden diff through the live sentinel network.

Integration check — needs the neuro-san server running with registries/sentinel.hocon and NIM
reachable. Not part of the pytest unit suite (that needs no server/LLM). Run:

    # terminal 1 — server (loads .env: AGENT_MANIFEST_FILE, AGENT_TOOL_PATH, AGENT_LLM_INFO_FILE, NVIDIA_API_KEY)
    PYTHONPATH=. python -m neuro_san.service.main_loop.server_main_loop
    # terminal 2
    PYTHONPATH=. python scripts/verify_b1.py

Builds a throwaway repo whose head modifies the auth module, seeds sly_data (event +
repo_workspace) like the Gateway will, drives the network over HTTP and asserts the ChangeProfile
lands with the auth sensitive flag. Exit 0 = PASS.
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


def _golden_repo():
    ws = tempfile.mkdtemp(prefix="sentinel-b1-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "app/__init__.py", "")
    _write(ws, "app/auth/__init__.py", "")
    _write(ws, "app/auth/login.py", "def authenticate(user, pw):\n    return user == 'a'\n")
    _write(ws, "app/api.py", "from app.auth.login import authenticate\n\n"
                             "def login_route(user, pw):\n    return authenticate(user, pw)\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    _write(ws, "app/auth/login.py",
           "def authenticate(user, pw):\n    if not user:\n        return False\n"
           "    return user == 'a' and pw == 'b'\n\n"
           "def check_token(token):\n    return bool(token)\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "add token check")
    return ws, base, _rev(ws)


def main(host="localhost", port=8080):
    ws, base, head = _golden_repo()
    event = {"event_id": "e1", "source": "manual",
             "repo": {"url": "file://x", "name": "python-payments-service", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-1",
                        "title": "add token check", "author": "dev"},
             "target_transition": {"from_env": "qa", "to_env": "staging"},
             "requested_by": "tester"}
    sly = {"run_id": "b1-run", "event": event, "repo_workspace": ws}

    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))

    cp = (mp.get_sly_data() or {}).get("change_profile")
    print("structure:", json.dumps(mp.get_structure()))
    if not cp:
        print("B1 FAIL: no change_profile returned")
        return 1
    paths = [f["path"] for f in cp.get("files", [])]
    flags = [f["flag"] for f in cp.get("sensitive_flags", [])]
    print("files:", paths, "| classification:", cp.get("classification"),
          "| sensitive_flags:", flags, "| new_functions:", cp.get("new_functions"))
    ok = "app/auth/login.py" in paths and "auth" in flags and cp.get("classification")
    print("B1 RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
