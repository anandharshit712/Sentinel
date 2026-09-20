"""P2 slice smoke test (08 §7): the four-dimension review cluster on a live network.

Needs the server running with registries/sentinel.hocon and NIM (see scripts/verify_b1.py):

    PYTHONPATH=. python scripts/verify_p2.py           # one run
    PYTHONPATH=. python scripts/verify_p2.py 3         # three runs, reports the decision rate

The fixture plants one finding for each dimension the cluster is supposed to cover:

    security    hardcoded AWS key            -> critical   (existing floor)
    performance db.query() inside a for loop -> medium     (lib/perf_rules)
    compliance  user email written to a log  -> high       (lib/license_rules)
    quality     handled by complexity metrics on the same file

Asserts the run still reaches a decision (08 §6 — two more agents on an already-long chain is the
risk of this phase, so the decision RATE is the headline number), that all three planted findings
are present, and that nothing outside security claimed `critical` (08 §5).
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid

from db import dao
from neuro_san.client.agent_session_factory import AgentSessionFactory
from neuro_san.client.streaming_input_processor import StreamingInputProcessor

BASE = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "app/orders.py": (
        "def load(ids, db):\n"
        "    return db.query(ids)\n"
    ),
}
HEAD = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "app/orders.py": (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "API_KEY = \"AKIA1234567890ABCDEF\"\n"
        "\n"
        "def load(ids, db, user):\n"
        "    log.info('orders for email=%s', user.email)\n"
        "    rows = []\n"
        "    for i in ids:\n"
        "        rows.append(db.query(i))\n"
        "    return rows\n"
    ),
}


def _sh(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True, text=True)


def _write(ws, rel, content):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)


def _rev(ws):
    return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _fixture():
    ws = tempfile.mkdtemp(prefix="sentinel-p2-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")
    _write(ws, "package.json", '{"name": "p2-fixture", "license": "MIT"}\n')
    _write(ws, "tests/test_calc.py",
           "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    for rel, c in BASE.items():
        _write(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    for rel, c in HEAD.items():
        _write(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "head")
    return ws, base, _rev(ws)


def _run(ws, base, head, label, host, port):
    event = {"event_id": f"p2-{label}", "source": "manual",
             "repo": {"url": "file://x", "name": "p2-demo", "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-p2",
                        "title": "orders loader", "author": "dev"},
             "target_transition": {"from_env": "dev", "to_env": "test"},
             "requested_by": "tester"}
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sentinel/p2-" + label))
    dao.ensure_run(run_id, event)
    sly = {"run_id": run_id, "event": event, "repo_workspace": ws}
    session = AgentSessionFactory().create_session("http", "sentinel", hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request("Process this DeliveryEvent: " + json.dumps(event), sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))
    return mp.get_structure(), (mp.get_sly_data() or {})


def _check(sd) -> tuple[bool, list[str]]:
    notes, ok = [], True
    report = sd.get("review_report") or {}
    findings = report.get("findings") or []
    cats = {f.get("category", "") for f in findings}
    dec = (sd.get("decision") or {}).get("decision")

    if not dec:
        return False, ["no decision — the chain did not finish (08 §6)"]

    for want, label in (("aws_access_key", "security: hardcoded AWS key"),
                        ("n_plus_one", "performance: query inside a loop"),
                        ("pii_exposure", "compliance: personal data in a log")):
        hit = want in cats
        ok = ok and hit
        notes.append(f"{'ok ' if hit else 'MISSING'} {label}")

    # 08 §5: only the security floor may say "critical"
    stray = [f for f in findings
             if f.get("severity") == "critical" and f.get("category") in
             ("n_plus_one", "n_plus_one_http", "sequential_await", "blocking_in_async",
              "unbounded_query", "quadratic_growth", "license_conflict", "pii_exposure",
              "deprecated_api")]
    if stray:
        ok = False
        notes.append(f"MISSING severity discipline — non-security critical: {[f['id'] for f in stray]}")

    # the contracts the two new agents are supposed to write
    for key in ("performance_findings", "compliance_findings"):
        notes.append(f"{'ok ' if sd.get(key) else 'absent'} {key} contract")

    notes.append(f"decision={dec} risk={(sd.get('risk_score') or {}).get('score')} "
                 f"health={report.get('pr_health_score')} findings={len(findings)}")
    return ok, notes


def main(argv) -> int:
    trials = int(argv[1]) if len(argv) > 1 else 1
    host, port = "localhost", 8080
    ws, base, head = _fixture()
    decided = passed = 0
    for i in range(trials):
        _struct, sd = _run(ws, base, head, f"t{i}", host, port)
        ok, notes = _check(sd)
        decided += 1 if (sd.get("decision") or {}).get("decision") else 0
        passed += 1 if ok else 0
        print(f"--- trial {i + 1}/{trials}: {'PASS' if ok else 'FAIL'}")
        for n in notes:
            print(f"    {n}")
    print(f"\nP2 RESULT: {passed}/{trials} pass, decision rate {decided}/{trials}")
    return 0 if passed == trials else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
