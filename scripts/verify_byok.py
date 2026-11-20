"""BYOK spike (12 §5): a run uses ONLY the key handed to it in sly_data, and that key leaks nowhere.

Start the neuro-san server with LLM_KEY_SOURCE=sly_data first, logging to a file:

    LLM_KEY_SOURCE=sly_data python scripts/run_server.py > <log> 2>&1
    PYTHONPATH=. python scripts/verify_byok.py <log> [<more files or dirs to search> ...]

Checks:
  1. no key in sly_data            -> the network cannot reach any LLM and produces no decision
                                      (the server's own env key is NOT used as a fallback)
  2. key in sly_data.llm_config    -> the happy-path run reaches "promote"
  3. the key never appears in the sly_data returned to the client, the server log, or any file
     under the extra paths given (thinking files, logs/)
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neuro_san.client.agent_session_factory import AgentSessionFactory
from neuro_san.client.streaming_input_processor import StreamingInputProcessor

from db import dao
from scripts.verify_b4 import _commit_pair, _rid


def _invoke(ws, base, head, label, llm_config):
    event = {"event_id": f"byok-{label}", "source": "manual",
             "repo": {"url": "file://x", "name": "byok-demo", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr", "title": "t", "author": "dev"},
             "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "verify_byok"}
    rid = _rid(f"byok-{label}")
    dao.ensure_run(rid, event)
    sly = {"run_id": rid, "event": event, "repo_workspace": ws}
    if llm_config:
        sly["llm_config"] = llm_config
    session = AgentSessionFactory().create_session("http", "sentinel", hostname="localhost", port=8080)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    try:
        for r in session.streaming_chat(req):
            mp.process_message(r.get("response", {}), r.get("type"))
    except Exception as e:      # a refused LLM can surface as a stream error; that is a "no decision"
        print(f"[{label}] stream ended with {type(e).__name__}: {str(e)[:160]}")
    return mp.get_sly_data() or {}, mp.get_answer() or ""


def _files(paths):
    for p in paths:
        p = Path(p)
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from (f for f in p.rglob("*") if f.is_file())


def main(argv) -> int:
    load_dotenv()
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    assert key, "NVIDIA_API_KEY must be set in .env (it is the key this spike hands over via sly_data)"
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and cond

    files = {"calc.py": "def add(a, b):\n    return a + b\n"}
    head = {"calc.py": files["calc.py"] + "\n\ndef subtract(a, b):\n    return a - b\n"}

    ws, b, h = _commit_pair(files, head)
    sd, answer = _invoke(ws, b, h, "nokey", None)
    check(not (sd.get("decision") or {}).get("decision"),
          f"without a sly_data key the run reaches no decision (answer: {answer[:120]!r})")

    ws, b, h = _commit_pair(files, head)
    sd, _ = _invoke(ws, b, h, "withkey", {"nvidia_api_key": key})
    dec = (sd.get("decision") or {}).get("decision")
    check(dec == "promote", f"with the key in sly_data.llm_config the run promotes (got {dec})")
    check("llm_config" not in sd and key not in json.dumps(sd, default=str),
          "the key is not in the sly_data returned to the client")

    leaks = [str(f) for f in _files(argv) if key in f.read_text(encoding="utf-8", errors="replace")]
    check(not leaks, f"the key appears in none of {len(list(_files(argv)))} log/thinking files"
                     + (f" — LEAKED in {leaks[:5]}" if leaks else ""))
    print("BYOK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
