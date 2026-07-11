"""Audit-mode + adaptive-fan-out smoke test (07 §6 B5). Full-repo review through the live network.

Needs the server running with registries/sentinel.hocon and NIM (see scripts/verify_b1.py) and
Postgres up (report_publisher/decision_logger persist). Run:

    PYTHONPATH=. python scripts/verify_audit.py

Run A (default budget): a small repo audited whole -> review_planner sizes 1 shard; review_report
                        carries coverage; a decision is recorded.
Run B (repo 'audit-smoke', tiny budget): the same style of repo with several dangerous-sink files
                        -> shard_count >= 2, and coverage.unscanned_shards == [] (every reviewer in
                        the parallel batch ran). Looped 3x — the batching-stability gate (07 §10).
Exit 0 = all PASS.
"""
import json
import os
import subprocess
import sys
import tempfile

from neuro_san.client.agent_session_factory import AgentSessionFactory
from neuro_san.client.streaming_input_processor import StreamingInputProcessor

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _sh(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True, text=True)


def _write(ws, rel, content):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
    open(p, "w").write(content)


def _rev(ws):
    return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _make_repo(files):
    ws = tempfile.mkdtemp(prefix="sentinel-audit-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")
    _write(ws, "tests/test_health.py", "def test_health_ok():\n    assert True\n")
    for rel, c in files.items():
        _write(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "init")
    return ws, _rev(ws)


def _run(ws, head, repo_name, to_env, host, port):
    # audit mode: base is the git empty-tree, so the whole repo reads as added.
    event = {"event_id": f"audit-{repo_name}", "source": "manual",
             "repo": {"url": "file://x", "name": repo_name, "default_branch": "main"},
             "change": {"base_sha": EMPTY_TREE, "head_sha": head, "branch": "audit",
                        "title": "full-repo audit", "author": "cli"},
             "target_transition": {"from_env": "dev", "to_env": to_env},
             "requested_by": "tester"}
    sly = {"run_id": f"audit-{repo_name}-{to_env}", "event": event, "repo_workspace": ws}
    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))
    return mp.get_structure(), (mp.get_sly_data() or {})


# a file with several dangerous sinks -> multiple hotspot lines
def _risky(mod):
    return (f"import os, pickle, hashlib\n\n"
            f"def run_{mod}(cmd, blob, pw):\n"
            f"    os.system('do ' + cmd)\n"
            f"    data = pickle.loads(blob)\n"
            f"    h = hashlib.md5(pw).hexdigest()\n"
            f"    q = \"SELECT * FROM t WHERE x = '\" + cmd + \"'\"\n"
            f"    return eval(cmd), data, h, q\n")


def _one(host, port, repo_name, to_env, files, expect_multi):
    ws, head = _make_repo(files)
    struct, sd = _run(ws, head, repo_name, to_env, host, port)
    plan = (sd.get("review_plan") or {}).get("metrics") or {}
    sc = plan.get("shard_count")
    report = sd.get("review_report") or {}
    cov = report.get("coverage") or {}
    dec = (sd.get("decision") or {}).get("decision")
    print(f"[{repo_name}] shard_count={sc} coverage={cov} decision={dec}")
    ok = sc is not None and cov and dec is not None
    if expect_multi:
        ok = ok and sc >= 2 and cov.get("unscanned_shards") == []
    else:
        ok = ok and sc == 1
    print(f"[{repo_name}]", "PASS" if ok else "FAIL")
    return ok


def main(host="localhost", port=8080):
    ok = True

    # Run A — default budget, small repo -> 1 shard, coverage present
    ok = _one(host, port, "audit-default", "test",
              {"app/calc.py": "def add(a, b):\n    return a + b\n"}, expect_multi=False) and ok

    # Run B — tiny budget (repo 'audit-smoke') + several sink files -> >= 2 shards; run 3x (stability)
    for i in range(3):
        print(f"--- audit-smoke run {i + 1}/3 ---")
        ok = _one(host, port, "audit-smoke", "test",
                  {"a/svc.py": _risky("a"), "b/svc.py": _risky("b"), "c/svc.py": _risky("c")},
                  expect_multi=True) and ok

    print("AUDIT RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
