"""Oracle-evidence smoke test (11 §7, O7): the gap in §1 must never reopen.

    PYTHONPATH=. python scripts/verify_o.py

No LLM and no network. Every tier verified here is deterministic by design — that was the point of
building them first — so this script is the one that can always be run, including on the days the
provider cannot be.

It asserts the behaviour the document exists for:

  * the §1 case — a docstring promising 15% over code applying 10% — comes back `disputed`,
    never `accepted`, and carries the arithmetic that shows why
  * a correct implementation stays completely silent (no finding, no dispute)
  * the differential tier separates behaviour this change altered from behaviour that predates it
  * a test with no independent witness is labelled `characterization`, never "verified"
  * the contradiction reaches the review report as a finding about the CODE
"""
import os
import subprocess
import sys
import tempfile

from coded_tools.sentinel.report_publisher_tool import _synthesize
from coded_tools.sentinel.blind_oracle_tool import BlindOracleTool
from coded_tools.sentinel.test_evaluator_tool import TestEvaluatorTool, classify
from lib import differential, spec_check
from lib.pyexec import fresh_import_env

BUGGY = ('def loyalty_discount(total, years):\n'
         '    """Members of 3+ years get 15% off. Everyone else pays full price."""\n'
         '    if years >= 3:\n'
         '        return round(total * 0.90, 2)\n'
         '    return total\n')
CORRECT = BUGGY.replace("0.90", "0.85")
TEST = ("from pricing import loyalty_discount\n\n\n"
        "def test_loyalty_discount():\n"
        "    assert loyalty_discount(100.0, 3) == {expected}\n"
        "    assert loyalty_discount(100.0, 2) == 100.0\n")


def _git(ws, *a):
    subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True)


def _repo_with_history():
    """A repo where `changed` is altered by the change and `stable` is not."""
    ws = tempfile.mkdtemp(prefix="verify-o-")
    subprocess.run(["git", "init", "-q", ws], check=True, capture_output=True)
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    path = os.path.join(ws, "m.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("def changed(x):\n    return x\n\n\ndef stable(x):\n    return x * 2\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    base = subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("def changed(x):\n    return x + 1\n\n\ndef stable(x):\n    return x * 2\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "head")
    return ws, base


def main() -> int:
    ok, notes = True, []

    def check(cond, label, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        notes.append(f"{'ok ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")

    # ---- the case this document exists for -------------------------------------------
    ws = tempfile.mkdtemp(prefix="verify-o-spec-")
    with open(os.path.join(ws, "pricing.py"), "w", encoding="utf-8") as fh:
        fh.write(BUGGY)
    r = TestEvaluatorTool().invoke(
        {"target_file": "pricing.py", "function": "loyalty_discount",
         "test_source": TEST.format(expected="90.0"), "repo_workspace": ws}, {})
    check(r.get("classification") == "disputed",
          "a test agreeing with contradicted code is DISPUTED", str(r.get("classification")))
    check(r.get("mutation_score", 0) >= 0.5,
          "and its mutation score is still reported, because it was never wrong",
          str(r.get("mutation_score")))
    mism = (r.get("oracle_evidence", {}).get("specification", {}) or {}).get("mismatches") or []
    check(bool(mism), "the dispute carries its arithmetic",
          mism[0]["evidence"] if mism else "no evidence attached")

    # ---- the same function, implemented as documented ---------------------------------
    ws2 = tempfile.mkdtemp(prefix="verify-o-ok-")
    with open(os.path.join(ws2, "pricing.py"), "w", encoding="utf-8") as fh:
        fh.write(CORRECT)
    r2 = TestEvaluatorTool().invoke(
        {"target_file": "pricing.py", "function": "loyalty_discount",
         "test_source": TEST.format(expected="85.0"), "repo_workspace": ws2}, {})
    check(r2.get("classification") != "disputed",
          "correct code produces no dispute", str(r2.get("classification")))
    check(not spec_check.check_function(CORRECT, "loyalty_discount"),
          "and no specification finding")

    # ---- the differential tier ---------------------------------------------------------
    repo, base = _repo_with_history()
    changed = differential.compare(repo, base, "m.py", "changed", "tests/t.py",
                                   "from m import changed\n\n\ndef test_c():\n    assert changed(1) == 2\n")
    check(changed["result"] == differential.CHANGED,
          "behaviour this change altered is identified", changed["result"])
    stable = differential.compare(repo, base, "m.py", "stable", "tests/t.py",
                                  "from m import stable\n\n\ndef test_s():\n    assert stable(2) == 4\n")
    check(stable["result"] == differential.UNCHANGED,
          "behaviour predating the change is identified", stable["result"])
    check(sorted(os.listdir(repo)) == [".git", "m.py"],
          "the workspace is never modified", str(sorted(os.listdir(repo))))

    # ---- the claim ---------------------------------------------------------------------
    check(classify(True, {"result": differential.UNKNOWN})[0] == "characterization",
          "no independent witness means 'characterization', never 'verified'")

    # ---- it is a finding about the CODE -------------------------------------------------
    report = _synthesize({
        "run_id": "verify-o", "repo_workspace": ws,
        "change_profile": {"files": [{"path": "pricing.py", "language": "python",
                                      "change_type": "modified",
                                      "functions_changed": [{"name": "loyalty_discount",
                                                             "line_start": 1, "line_end": 5}]}]}})
    specs = [f for f in report["findings"] if f["category"] == "spec_implementation_mismatch"]
    check(len(specs) == 1, "the contradiction is reported as a review finding",
          f"{len(specs)} found")
    check(specs and specs[0]["source"] == "tool",
          "deterministic, so a drifting reviewer cannot lose it")
    check(specs and "stale" in specs[0]["explanation"],
          "and it does not claim which side is wrong")

    # ---- Tier 2: the blind oracle ------------------------------------------------------
    oracle = BlindOracleTool()
    ctx = oracle.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                         "repo_workspace": ws}, {})
    check(isinstance(ctx, dict) and "0.90" not in (ctx.get("module_without_body") or ""),
          "the oracle never sees the implementation")
    check(isinstance(ctx, dict) and "15%" in (ctx.get("docstring") or ""),
          "but it does see the stated intent")
    disputed = oracle.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                              "repo_workspace": ws,
                              "expectations": [{"args": [100.0, 3], "expected": 85.0,
                                                "because": "the docstring promises 15% off"}]}, {})
    check(disputed.get("verdict") == "disputed",
          "an expectation derived from the docs disputes the implementation",
          str(disputed.get("verdict")))
    confirmed = oracle.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                               "repo_workspace": ws2,
                               "expectations": [{"args": [100.0, 3], "expected": 85.0}]}, {})
    check(confirmed.get("verdict") == "confirms_intent",
          "and confirms code that matches its docs", str(confirmed.get("verdict")))

    # ---- the latent bug this work uncovered --------------------------------------------
    import subprocess as _sp
    ws3 = tempfile.mkdtemp(prefix="verify-o-pyc-")
    mod = os.path.join(ws3, "m.py")
    with open(mod, "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 90.0\n")
    _sp.run([sys.executable, "-c", "import m; print(m.f())"], cwd=ws3, capture_output=True,
            check=False)
    with open(mod, "w", encoding="utf-8") as fh:          # same size, same second
        fh.write("def f():\n    return 85.0\n")
    fresh = _sp.run([sys.executable, "-c", "import m; print(m.f())"], cwd=ws3, capture_output=True,
                    text=True, check=False, env=fresh_import_env()).stdout.strip()
    check(fresh == "85.0",
          "a rewritten module is never run from stale bytecode (mutation scores depend on it)",
          fresh)

    for n in notes:
        print(f"    {n}")
    print(f"\nORACLE RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
