"""B3 slice smoke test (07 §6): full pipeline incl. test selection + execution → TestResults.

Needs the server running with registries/sentinel.hocon and NIM (see scripts/verify_b1.py). Run:

    PYTHONPATH=. python scripts/verify_b3.py

Builds a repo with a module + its test + a pytest marker, changes the module in head, drives the
whole network, and asserts a real pytest subset ran and TestResults landed in sly_data. Exit 0 = PASS.
The repo name is intentionally not in repo_config so no smoke id points at a non-existent test.
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
    ws = tempfile.mkdtemp(prefix="sentinel-b3-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")  # makes test_runner detect pytest
    _write(ws, "calc.py", "def add(a, b):\n    return a + b\n")
    _write(ws, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    _write(ws, "calc.py", "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n")
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "add multiply")
    return ws, base, _rev(ws)


def main(host="localhost", port=8080):
    ws, base, head = _repo()
    event = {"event_id": "e3", "source": "manual",
             "repo": {"url": "file://x", "name": "b3-demo", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-3",
                        "title": "add multiply", "author": "dev"},
             "target_transition": {"from_env": "qa", "to_env": "staging"},
             "requested_by": "tester"}
    sly = {"run_id": "b3-run", "event": event, "repo_workspace": ws}

    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))

    returned = mp.get_sly_data() or {}
    print("frontman structure:", json.dumps(mp.get_structure()))
    tp = returned.get("test_plan")
    tr = returned.get("test_results")
    if tp:
        print("test_plan selected:", [s["test_id"] for s in tp.get("selected", [])],
              "| confidence:", tp.get("selection_confidence"))
    if not tr:
        print("B3 FAIL: no test_results; sly_data keys=", list(returned))
        return 1
    print("runner:", tr.get("runner"), "| totals:", tr.get("totals"),
          "| timed_out:", tr.get("timed_out"), "| stage_failure:", tr.get("stage_failure"))
    totals = tr.get("totals") or {}
    ok = tr.get("runner") == "pytest" and totals.get("passed", 0) >= 1 and not tr.get("timed_out")
    print("B3 RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
