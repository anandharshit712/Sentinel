"""P3 slice smoke test (09 §7): gap -> generated test -> mutation-proven proposal.

Needs the Neuro-SAN server running with registries/ (see scripts/verify_b1.py):

    PYTHONPATH=. python scripts/verify_p3.py          # one run
    PYTHONPATH=. python scripts/verify_p3.py 3        # three, for a success rate

The fixture is a function with a branch its existing test never reaches. The pipeline part is run
in process (test_runner -> coverage_gap) because that half is deterministic and already covered by
unit tests; what needs a live network is the generation loop.

Asserts what the phase claims, not merely that something ran:
  * the uncovered branch is found
  * a test is generated that PASSES against unmodified code
  * that test CATCHES injected bugs (mutation score above the threshold)
  * the proposal is recorded with its evidence, and nothing is written into the repository
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

# `apply_coupon` has a branch the existing test never reaches: the expiry path.
SOURCE = (
    "def apply_coupon(total, code, days_left):\n"
    "    if code == 'SAVE10' and days_left > 0:\n"
    "        return round(total * 0.9, 2)\n"
    "    return total\n"
)
EXISTING_TEST = (
    "from shop import apply_coupon\n\n\n"
    "def test_no_coupon():\n"
    "    assert apply_coupon(100.0, 'NONE', 5) == 100.0\n"
)


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
    """Two real commits, so the differential tier has a base to compare against.

    This previously committed once and the event carried a fabricated base SHA, so Tier 1 reported
    `unknown` on every live run — the script could not have failed on a broken differential tier,
    which is the same shape as a verification that cannot fail.
    """
    ws = tempfile.mkdtemp(prefix="sentinel-p3-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    _write(ws, "requirements.txt", "pytest\n")
    # base: no coupon logic at all
    _write(ws, "shop.py", "def apply_coupon(total, code, days_left):\n    return total\n")
    _write(ws, "tests/test_shop.py", EXISTING_TEST)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base")
    base = _rev(ws)
    # head: the change under review adds the discount branch
    _write(ws, "shop.py", SOURCE)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "head")
    return ws, base, _rev(ws)


def _profile():
    return {"files": [{"path": "shop.py", "language": "python", "change_type": "modified",
                       "functions_changed": [{"name": "apply_coupon", "kind": "function",
                                              "line_start": 1, "line_end": 4, "is_new": True}]}],
            "sensitive_flags": [], "classification": "feature",
            "loc_added": 4, "loc_removed": 0, "blast_radius": {"count": 0}}


def _prepare(ws, run_id, base_sha, head_sha):
    """Deterministic half, in process: run the tests with coverage, then find the gap."""
    from coded_tools.sentinel.coverage_gap_tool import CoverageGapTool
    from coded_tools.sentinel.test_runner_tool import TestRunnerTool

    event = {"event_id": f"p3-{run_id[:8]}", "source": "manual",
             "repo": {"url": "file://x", "name": "p3-demo", "default_branch": "main"},
             "change": {"base_sha": base_sha, "head_sha": head_sha, "branch": "pr-p3",
                        "title": "coupon", "author": "dev"},
             "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "tester"}
    dao.ensure_run(run_id, event)
    sly = {"run_id": run_id, "event": event, "repo_workspace": ws, "change_profile": _profile()}
    sly["test_results"] = TestRunnerTool().invoke({}, sly)
    gaps = CoverageGapTool().invoke({}, sly)
    sly["coverage_gaps"] = gaps
    return sly, gaps


def _generate(sly, host, port):
    session = AgentSessionFactory().create_session("http", "sentinel_testgen",
                                                   hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    req = proc.formulate_chat_request(
        "Write a test for the highest-priority coverage gap in this run.", sly)
    for r in session.streaming_chat(req):
        mp.process_message(r.get("response", {}), r.get("type"))
    return mp.get_structure(), (mp.get_sly_data() or {})


def main(argv) -> int:
    trials = int(argv[1]) if len(argv) > 1 else 1
    host, port = "localhost", 8080
    passes = 0
    for i in range(trials):
        # Unique per EXECUTION, not per trial index: a deterministic id made trial 1 read the
        # proposal left by the previous run of this script and report a pass for work it had not
        # done. A verification that can pass on stale rows verifies nothing.
        run_id = str(uuid.uuid4())
        ws, base_sha, head_sha = _fixture()
        sly, gaps = _prepare(ws, run_id, base_sha, head_sha)
        ok, notes = True, []

        found = [g for g in gaps.get("gaps", []) if g["status"] != "covered"]
        notes.append(f"{'ok ' if found else 'MISSING'} gap found: "
                     f"{found[0]['function'] + ' ' + found[0]['status'] if found else 'none'}")
        ok = ok and bool(found)

        struct, out = _generate(sly, host, port) if found else ({}, {})
        proposals = dao.list_test_proposals(run_id)
        notes.append(f"{'ok ' if proposals else 'MISSING'} proposal recorded ({len(proposals)})")
        ok = ok and bool(proposals)

        if proposals:
            p = proposals[0]
            ev = p["evaluation"] if isinstance(p["evaluation"], dict) else json.loads(p["evaluation"])
            score = p["mutation_score"] or 0.0
            notes.append(f"{'ok ' if p['verdict'] == 'accepted' else 'WEAK'} verdict={p['verdict']} "
                         f"score={score:.2f} caught={len(ev.get('caught') or [])}/"
                         f"{ev.get('mutants_total')}")
            notes.append(f"{'ok ' if p['status'] == 'proposed' else 'WRONG'} status={p['status']} "
                         f"(adoption is a human action)")
            ok = ok and p["verdict"] == "accepted" and score >= 0.5

        # No GENERATED TEST may be written into the repository under test. Coverage artifacts
        # (.coverage) are written by our own test_runner --cov earlier in this script, so they are
        # not evidence of the generator touching anything.
        # Ignore what OUR OWN earlier pytest run leaves behind (.coverage, __pycache__); the
        # question is only whether the generator wrote a test file into the repository.
        noise = {".git", "requirements.txt", "shop.py", "tests", "__pycache__", ".pytest_cache"}
        stray = [f for f in os.listdir(ws) if f not in noise and not f.startswith(".coverage")]
        stray += [f for f in os.listdir(os.path.join(ws, "tests"))
                  if f not in {"test_shop.py", "__pycache__"}]
        notes.append(f"{'ok ' if not stray else 'WROTE FILES'} repository untouched"
                     + (f" — found {stray}" if stray else ""))
        ok = ok and not stray

        print(f"--- trial {i + 1}/{trials}: {'PASS' if ok else 'FAIL'}")
        for n in notes:
            print(f"    {n}")
        if struct:
            print(f"    agent: {json.dumps(struct)[:200]}")
        passes += 1 if ok else 0

    print(f"\nP3 RESULT: {passes}/{trials} pass")
    return 0 if passes == trials else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
