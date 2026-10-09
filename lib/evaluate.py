"""Mutation-grounded evaluation of a test — the standalone core (P5; 09 §4 P3.6, 11).

Does a test actually catch bugs, and is what it expects actually right? A passing test proves
nothing on its own, so the verdict comes from experiments, not an opinion:

  1. **It must pass against unmodified code.** A test that fails on correct code is worse than no
     test — it trains people to ignore red.
  2. **Break the function on purpose** (`lib.mutate`, one edit per mutant) and re-run the test.
     Every mutant the test fails on is a real bug it would have caught.
  3. **Mutation score = caught / total.** That number, and the list of what it missed, is the
     evidence a reviewer sees.
  4. **Reject tautologies** — `assert True`, `assert 1 == 1` — cheap to detect structurally.
  5. **Ask a second witness** (`lib.differential`): does the test also pass at the base commit?
     Mutation testing measures *sensitivity* — the implementation is its only witness, so a test
     that asserts a bug scores as well as one that asserts the fix.
  6. **Ask the documentation** (`lib.spec_check`): does the code contradict its own docstring?

No Neuro-SAN, no database, no model: stdlib + pytest + git. Sentinel's `test_evaluator` coded tool
is a thin wrapper over `evaluate()`; this module is also usable on its own against any Python repo:

    python -m lib.evaluate --repo . --target shop.py --function discount --test tests/test_shop.py
    python -m lib.evaluate ... --base <sha> --json
    python -m lib.evaluate --self-check

Everything happens inside a **copy** of the repository, made per evaluation and deleted after, so
the repository is never written to. The test is still code — running it executes it on this
machine, sandboxed only by a temp directory and a timeout.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

from lib import differential as differential_mod
from lib import mutate, spec_check
from lib.pyexec import fresh_import_env

logger = logging.getLogger("lib.evaluate")

DEFAULT_MIN_SCORE = 0.5     # 09 §5 — a guess to be replaced by a measured threshold
PER_RUN_TIMEOUT = 120       # one pytest invocation over a single test file
MAX_MUTANTS = 12            # each costs a pytest run; the campaign must stay interactive
DEFAULT_TEST_PATH = "tests/test_sentinel_generated.py"


def is_tautology(test_source: str) -> bool:
    """True when no assertion compares anything to a non-literal.

    `assert True`, `assert 1 == 1`, `assert "x" in "xyz"` — all pass forever and detect nothing.
    An assertion that mentions any name or call is taken as real; this is a cheap structural
    screen, not a semantic judgement.
    """
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return False                      # unparseable is a different failure, reported elsewhere
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if not asserts:
        return True                       # a test with no assertion asserts nothing
    for a in asserts:
        for node in ast.walk(a.test):
            if isinstance(node, (ast.Name, ast.Call, ast.Attribute, ast.Subscript)):
                return False
    return True


def _run_pytest(repo: str, test_rel: str, timeout: int = PER_RUN_TIMEOUT) -> tuple[bool, str]:
    """(passed, tail_of_output) for one test file. A timeout counts as not passing."""
    cmd = [sys.executable, "-m", "pytest", test_rel, "-q", "-p", "no:cacheprovider",
           "-x", "--no-header"]
    try:
        # fresh_import_env: mutants are size-preserving often enough that stale bytecode would
        # silently record a live mutant as "missed" and deflate the score (lib/pyexec.py).
        r = subprocess.run(cmd, cwd=repo, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout, check=False,
                           env=fresh_import_env(repo=repo))
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out[-400:]


def classify(mutation_ok: bool, differential: Dict[str, Any] | None,
             spec_mismatches: List[Dict[str, Any]] | None = None,
             intent_verdict: str | None = None) -> tuple[str, str]:
    """What this test is, and what evidence says so (11 §6).

    `accepted` used to mean "the test agrees with the implementation", which reads as "the test is
    correct" and is exactly the overclaim that let a test asserting a bug pass with 0.83. A
    classification names the witness instead:

      regression_guard  the behaviour predates this change, so the test cannot have encoded a bug
                        this change introduced
      change_documented the test asserts behaviour this change altered — real evidence about the
                        change, and the point at which intent (Tier 2) decides whether it is right
      characterization  nothing independent of the implementation vouched for it. Adoptable, but
                        it locks in current behaviour, bugs included, and says so
    """
    # A dispute outranks everything, including a perfect mutation score. If the code and its
    # documentation disagree, a test generated from the code takes the code's side by construction —
    # which is the failure this whole design exists to stop. It is also a finding about the CODE,
    # not merely a reason to discard a test (11 §5).
    if spec_mismatches:
        m = spec_mismatches[0]
        return "disputed", (f"{m['evidence']}. A test written from the implementation would assert "
                            f"the implemented behaviour, so it is not safe to adopt until a human "
                            f"says which side is right")
    if not mutation_ok:
        return "rejected", "the test does not detect enough injected bugs to be worth keeping"
    # A confirmed intent is an independent witness and outranks the differential tiers: the
    # documentation predicted what the code returns, checked without sight of the implementation.
    # Reporting that as "characterization — unverified" would understate the evidence as badly as
    # "accepted" once overstated it.
    if intent_verdict == "confirms_intent":
        return "intent_confirmed", ("an oracle reading only the documentation predicted the values "
                                    "the implementation returns, so the code and its stated intent "
                                    "agree")
    result = (differential or {}).get("result")
    if result == differential_mod.UNCHANGED:
        return "regression_guard", ("locks behaviour that predates this change — it cannot encode a "
                                    "bug this change introduced")
    if result == differential_mod.CHANGED:
        return "change_documented", ("asserts behaviour this change altered; whether that change "
                                     "was intended is a question for the author")
    if result == differential_mod.NEW_CODE:
        return "characterization", ("new code, so there is no earlier behaviour to compare with — "
                                    "this describes what the code does today, not what it should do")
    return "characterization", ("no independent witness: this describes what the code does today, "
                                "bugs included, not what it should do")


def _rejected(classification_reason: str, reason: str, **extra: Any) -> Dict[str, Any]:
    return {"verdict": "rejected", "classification": "rejected",
            "classification_reason": classification_reason, "reason": reason,
            "mutation_score": 0.0, "caught": [], "missed": [], "mutants_total": 0,
            "oracle_evidence": {}, **extra}


def evaluate(workspace: str, target_file: str, function: str, test_source: str,
             test_path: str = DEFAULT_TEST_PATH, base_sha: Optional[str] = None,
             min_score: float = DEFAULT_MIN_SCORE, max_mutants: int = MAX_MUTANTS) -> Dict[str, Any]:
    """Run the whole experiment for one test over one function. Raises only on bad arguments.

    `workspace` is a checkout of the repository at the code under test. It is copied, never
    written to; the differential tier reads its git history through a throwaway worktree.
    """
    if is_tautology(test_source):
        return _rejected("asserts nothing about the code under test",
                         "tautological: no assertion depends on the code under test")

    sandbox = tempfile.mkdtemp(prefix="sentinel-eval-")
    try:
        repo = os.path.join(sandbox, "repo")
        shutil.copytree(workspace, repo, symlinks=False,
                        ignore=shutil.ignore_patterns(".git", "node_modules", ".venv",
                                                      "__pycache__", ".pytest_cache"))
        abs_test = os.path.join(repo, test_path.replace("/", os.sep))
        os.makedirs(os.path.dirname(abs_test), exist_ok=True)
        with open(abs_test, "w", encoding="utf-8") as fh:
            fh.write(test_source)

        abs_target = os.path.join(repo, target_file.replace("/", os.sep))
        if not os.path.exists(abs_target):
            raise FileNotFoundError(f"target file {target_file} not found in the workspace")
        with open(abs_target, encoding="utf-8") as fh:
            original = fh.read()

        started = time.perf_counter()
        # ---- 1. must pass on correct code -----------------------------------------------
        passed, tail = _run_pytest(repo, test_path)
        if not passed:
            return _rejected("red against unmodified code",
                             "fails against unmodified code — a test that is red on correct "
                             "code is worse than no test", output=tail)

        # ---- 2. mutation campaign --------------------------------------------------------
        mutants = mutate.generate(original, function, limit=max_mutants)
        caught: List[Dict[str, str]] = []
        missed: List[Dict[str, str]] = []
        for m in mutants:
            with open(abs_target, "w", encoding="utf-8") as fh:
                fh.write(m.source)
            still_passes, _ = _run_pytest(repo, test_path)
            entry = {"id": m.id, "operator": m.operator, "description": m.description}
            (missed if still_passes else caught).append(entry)
        with open(abs_target, "w", encoding="utf-8") as fh:
            fh.write(original)

        total = len(mutants)
        score = round(len(caught) / total, 3) if total else 0.0
        elapsed = round(time.perf_counter() - started, 1)

        if total == 0:
            verdict, reason = "inconclusive", (f"no mutants could be generated for {function} — "
                                               "nothing to measure against")
        elif score >= min_score:
            verdict, reason = "accepted", (f"caught {len(caught)} of {total} injected bugs "
                                           f"(threshold {min_score})")
        else:
            verdict, reason = "rejected", (f"caught only {len(caught)} of {total} injected bugs "
                                           f"(threshold {min_score})")

        # ---- 3. the second witness: what did this change actually alter? (11 §4) --------
        # Runs on the ORIGINAL workspace (which has git history), not the sandbox copy.
        diff_evidence = differential_mod.compare(
            workspace, base_sha, target_file, function, test_path, test_source,
            head_passed=True) if base_sha else {
                "tier": "differential", "result": differential_mod.UNKNOWN,
                "reason": "no base commit supplied for this run"}

        # ---- 4. does the code agree with its own documentation? (11 §3 Tier 3) ----------
        spec_mismatches = spec_check.check_function(original, function)

        classification, class_reason = classify(verdict == "accepted", diff_evidence,
                                                spec_mismatches)
        oracle_evidence = {
            "mutation": {"tier": "mutation", "score": score, "caught": len(caught),
                         "total": total, "threshold": min_score,
                         "witness": "the implementation itself — sensitivity, not correctness"},
            "differential": diff_evidence,
            "specification": {"tier": "specification", "mismatches": spec_mismatches,
                              "witness": "the docstring's stated intent", "checked": True},
        }
        logger.info("evaluate %s -> %s / %s (score %.2f, %d mutants, %.1fs)",
                    function, verdict, classification, score, total, elapsed)
        return {"verdict": verdict, "classification": classification,
                "classification_reason": class_reason, "oracle_evidence": oracle_evidence,
                "reason": reason, "mutation_score": score,
                "caught": caught, "missed": missed, "mutants_total": total,
                "duration_seconds": elapsed, "threshold": min_score}
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# ---- CLI ---------------------------------------------------------------------------------------

def _render(r: Dict[str, Any]) -> str:
    lines = [f"verdict         {r['verdict']}",
             f"classification  {r['classification']} — {r['classification_reason']}",
             f"mutation score  {r['mutation_score']:.2f}  ({len(r['caught'])}/{r['mutants_total']} "
             f"injected bugs caught)"]
    for m in r["missed"]:
        lines.append(f"  missed   {m['id']:<14} {m['description']}")
    ev = r.get("oracle_evidence") or {}
    if ev.get("differential"):
        d = ev["differential"]
        lines.append(f"differential    {d.get('result')} — {d.get('reason', '')}")
    for m in (ev.get("specification") or {}).get("mismatches", []):
        lines.append(f"specification   {m['evidence']}")
    if r.get("output"):
        lines.append("pytest output (tail):\n" + r["output"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lib.evaluate",
        description="Does this test catch bugs, and does it agree with anything but the code?")
    ap.add_argument("--repo", help="repository checkout containing the code under test")
    ap.add_argument("--target", help="file holding the function, relative to --repo")
    ap.add_argument("--function", help="function (or Class.method) the test is for")
    ap.add_argument("--test", help="the test file to evaluate (any path)")
    ap.add_argument("--test-path", help=f"where the test sits inside the repo while it runs "
                                        f"(default: the test's own path if inside --repo, "
                                        f"else {DEFAULT_TEST_PATH})")
    ap.add_argument("--base", help="base commit SHA, enables the differential witness")
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    ap.add_argument("--max-mutants", type=int, default=MAX_MUTANTS)
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--self-check", action="store_true", help="run the built-in self-check")
    a = ap.parse_args(argv)

    if a.self_check:
        demo()
        return 0
    if not (a.repo and a.target and a.function and a.test):
        ap.error("--repo, --target, --function and --test are required")

    repo = os.path.abspath(a.repo)
    test_abs = os.path.abspath(a.test)
    test_path = a.test_path
    if not test_path:
        rel = os.path.relpath(test_abs, repo)
        test_path = rel.replace(os.sep, "/") if not rel.startswith("..") else DEFAULT_TEST_PATH
    with open(test_abs, encoding="utf-8") as fh:
        source = fh.read()

    r = evaluate(repo, a.target, a.function, source, test_path=test_path, base_sha=a.base,
                 min_score=a.min_score, max_mutants=a.max_mutants)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # a cp1252 Windows console mangles the dashes
    print(json.dumps(r, indent=2) if a.json else _render(r))
    # exit status a CI step can gate on: adoptable evidence or not
    return 0 if r["verdict"] == "accepted" and r["classification"] != "disputed" else 1


def demo() -> None:
    """Self-check against a real workspace: a good test, a weak test, a tautology, a red test,
    a documented-rate dispute, and the CLI's exit status."""
    ws = tempfile.mkdtemp(prefix="sentinel-eval-demo-")
    src = ("def discount(qty, unit, member):\n"
           "    if qty > 10 and member:\n"
           "        return qty * unit * 0.9\n"
           "    return qty * unit\n")
    with open(os.path.join(ws, "shop.py"), "w", encoding="utf-8") as fh:
        fh.write(src)

    good = ("from shop import discount\n\n\n"
            "def test_member_bulk_discount():\n"
            "    assert discount(11, 10.0, True) == 99.0\n"
            "    assert discount(10, 10.0, True) == 100.0\n"
            "    assert discount(11, 10.0, False) == 110.0\n")
    r = evaluate(ws, "shop.py", "discount", good)
    assert r["verdict"] == "accepted", r
    assert r["mutation_score"] >= 0.5 and r["caught"] and r["mutants_total"] > 0, r
    assert r["classification"] == "characterization", "no base, no doc: nothing independent vouched"

    r2 = evaluate(ws, "shop.py", "discount", "def test_nothing():\n    assert True\n")
    assert r2["verdict"] == "rejected" and "tautolog" in r2["reason"], r2
    assert r2["mutants_total"] == 0, "a tautology is rejected before any mutant is run"

    weak = ("from shop import discount\n\n\n"
            "def test_returns_a_number():\n"
            "    assert isinstance(discount(1, 2.0, False), float)\n")
    r3 = evaluate(ws, "shop.py", "discount", weak)
    assert r3["verdict"] == "rejected" and r3["mutation_score"] < 0.5, r3

    red = "from shop import discount\n\n\ndef test_w():\n    assert discount(1, 2.0, False) == 999\n"
    r4 = evaluate(ws, "shop.py", "discount", red)
    assert r4["verdict"] == "rejected" and "unmodified" in r4["reason"], r4

    # the documented-rate witness: a thorough test of a function that contradicts its docstring
    with open(os.path.join(ws, "loyal.py"), "w", encoding="utf-8") as fh:
        fh.write('def loyalty(total, years):\n    """Members of 3+ years get 15% off."""\n'
                 '    if years >= 3:\n        return round(total * 0.90, 2)\n    return total\n')
    bug_test = ("from loyal import loyalty\n\n\ndef test_l():\n"
                "    assert loyalty(100, 3) == 90.0\n    assert loyalty(100, 2) == 100\n")
    r5 = evaluate(ws, "loyal.py", "loyalty", bug_test, test_path="tests/test_loyal.py")
    assert r5["verdict"] == "accepted", "it IS a sensitive test of the code as written"
    assert r5["classification"] == "disputed", r5   # ...that asserts the code's side of a dispute

    # the workspace itself is never written to
    assert sorted(os.listdir(ws)) == ["loyal.py", "shop.py"], os.listdir(ws)
    with open(os.path.join(ws, "shop.py"), encoding="utf-8") as fh:
        assert fh.read() == src, "the original source must be untouched"

    # CLI: exit 0 on adoptable evidence, 1 otherwise (a dispute is not adoptable)
    tdir = tempfile.mkdtemp(prefix="sentinel-eval-cli-")
    for name, body in (("good.py", good), ("bug.py", bug_test)):
        with open(os.path.join(tdir, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert main(["--repo", ws, "--target", "shop.py", "--function", "discount",
                     "--test", os.path.join(tdir, "good.py")]) == 0
        assert main(["--repo", ws, "--target", "loyal.py", "--function", "loyalty",
                     "--test", os.path.join(tdir, "bug.py"), "--json"]) == 1
    assert "mutation score" in out.getvalue() and '"disputed"' in out.getvalue()

    shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(tdir, ignore_errors=True)
    print("lib/evaluate.py OK")


if __name__ == "__main__":
    sys.exit(main())
