"""Demo smart test selection at the LIVE Gateway (:8000) on a multi-test repo.

Builds a repo with 3 modules + 3 test files (6 tests total), changes ONE module (beta), and
fires it at the Gateway. The change maps ONLY to test_beta (2 tests) -> the runner executes 2 of
6 and excludes 4. Open the printed run URL to see the "ran 2 / 6 · 4 excluded" card + case list.

    # servers up (run.ps1), then:
    PYTHONPATH=. python scripts/demo_selection.py
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

GW = "http://localhost:8000"

BASE = {
    "requirements.txt": "pytest\n",
    "alpha.py": "def a1(x):\n    return x + 1\n\n\ndef a2(x):\n    return x + 2\n\n\ndef a3(x):\n    return x + 3\n",
    "beta.py": "def b1(x):\n    return x * 2\n\n\ndef b2(x):\n    return x * 3\n",
    "gamma.py": "def g1(x):\n    return -x\n",
    "tests/test_alpha.py": "from alpha import a1, a2, a3\n\n\ndef test_a1():\n    assert a1(1) == 2\n\n\ndef test_a2():\n    assert a2(1) == 3\n\n\ndef test_a3():\n    assert a3(1) == 4\n",
    "tests/test_beta.py": "from beta import b1, b2\n\n\ndef test_b1():\n    assert b1(2) == 4\n\n\ndef test_b2():\n    assert b2(2) == 6\n",
    "tests/test_gamma.py": "from gamma import g1\n\n\ndef test_g1():\n    assert g1(2) == -2\n",
}
# head: change ONLY beta.py -> maps to test_beta only
HEAD_BETA = "def b1(x):\n    return x * 2\n\n\ndef b2(x):\n    return x * 3\n\n\ndef b3(x):\n    return x * 4\n"


def _sh(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True, text=True)


def _wr(ws, rel, c):
    import os
    p = os.path.join(ws, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(c)


def _rev(ws):
    return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def main():
    ws = tempfile.mkdtemp(prefix="sentinel-sel-")
    _sh(ws, "init", "-q"); _sh(ws, "config", "user.email", "t@t"); _sh(ws, "config", "user.name", "t")
    for rel, c in BASE.items():
        _wr(ws, rel, c)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "base"); base = _rev(ws)
    _wr(ws, "beta.py", HEAD_BETA)
    _sh(ws, "add", "-A"); _sh(ws, "commit", "-qm", "change beta"); head = _rev(ws)

    ev = {"event_id": f"sel-{int(time.time()*1000)}", "source": "manual",
          "repo": {"url": ws, "name": "selection-demo", "default_branch": "main"},
          "change": {"base_sha": base, "head_sha": head, "branch": "pr-sel",
                     "title": "change beta only", "author": "dev"},
          "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "demo"}
    body = json.dumps({"event": ev, "repo_workspace": ws}).encode()
    r = urllib.request.urlopen(urllib.request.Request(f"{GW}/api/v1/simulate", body, {"Content-Type": "application/json"}))
    rid = json.load(r)["run_id"]
    print(f"\n  run: {GW}/runs/{rid}\n  (change touched beta.py only -> expect 2 of 6 tests run)\n")

    for _ in range(120):
        d = json.load(urllib.request.urlopen(f"{GW}/api/v1/runs/{rid}"))
        if d["run"]["state"] in ("done", "failed"):
            break
        time.sleep(2)
    tr = d.get("test_results") or {}
    tp = d.get("test_plan") or {}
    print("  state:", d["run"]["state"])
    print("  selection_mode:", tr.get("selection_mode"))
    print("  suite_total:", tr.get("suite_total"), " executed:", tr.get("executed"), " excluded:", tr.get("excluded"))
    print("  selected (test_plan):", [s.get("test_id") for s in tp.get("selected", [])])
    print("  cases run:", [c.get("test_id") for c in tr.get("cases", [])])
    ok = tr.get("selection_mode") == "subset" and tr.get("executed") == 2 and tr.get("suite_total") == 6
    print("\n  SELECTION DEMO:", "PASS" if ok else "CHECK (see numbers above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
