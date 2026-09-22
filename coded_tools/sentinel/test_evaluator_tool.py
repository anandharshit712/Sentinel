"""test_evaluator_tool (09 §4 P3.6) — does a proposed test actually catch bugs?

The gate that makes "AI-generated tests" worth a human's attention. A generated test that passes
has proven nothing: it may assert `add(1, 2) == 3` while the branch it was written for is never
touched, or assert nothing at all. So the verdict comes from an experiment, not an opinion:

  1. **It must pass against unmodified code.** A test that fails on correct code is worse than no
     test — it trains people to ignore red.
  2. **Break the function on purpose** (`lib.mutate`, one edit per mutant) and re-run the test.
     Every mutant the test fails on is a real bug it would have caught.
  3. **Mutation score = caught / total.** That number, and the list of what it missed, is the
     evidence a reviewer sees.
  4. **Reject tautologies** — `assert True`, `assert 1 == 1` — the classic generated-test failure
     mode, cheap to detect structurally and not worth a mutation campaign.

Everything happens inside a **copy** of the run workspace, made per evaluation and deleted after,
so neither the source repository nor the original workspace is ever written to. Note what this
still means: the test is code a model wrote, and running it executes that code on this machine,
sandboxed only by a temp directory and a timeout (09 §3).
"""
from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import mutate

logger = logging.getLogger("coded_tools.test_evaluator")

DEFAULT_MIN_SCORE = 0.5     # 09 §5 — a guess to be replaced by a measured threshold
PER_RUN_TIMEOUT = 120       # one pytest invocation over a single test file
MAX_MUTANTS = 12            # each costs a pytest run; the campaign must stay interactive


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
        r = subprocess.run(cmd, cwd=repo, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out[-400:]


class TestEvaluatorTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        sandbox = None
        try:
            target_file = args.get("target_file")
            function = args.get("function")
            test_source = args.get("test_source")
            test_path = args.get("test_path") or "tests/test_sentinel_generated.py"
            workspace = args.get("repo_workspace") or sly_data.get("repo_workspace")
            min_score = float(args.get("min_score", DEFAULT_MIN_SCORE))
            if not (target_file and function and test_source and workspace):
                return "Error: target_file, function, test_source and a repo workspace are required"

            if is_tautology(test_source):
                return {"verdict": "rejected", "reason": "tautological: no assertion depends on "
                                                         "the code under test",
                        "mutation_score": 0.0, "caught": [], "missed": [], "mutants_total": 0}

            # Work on a copy: nothing here may touch the run workspace or the source repo.
            sandbox = tempfile.mkdtemp(prefix="sentinel-eval-")
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
                return f"Error: target file {target_file} not found in the workspace"
            with open(abs_target, encoding="utf-8") as fh:
                original = fh.read()

            started = time.perf_counter()
            # ---- 1. must pass on correct code -------------------------------------------
            passed, tail = _run_pytest(repo, test_path)
            if not passed:
                return {"verdict": "rejected",
                        "reason": "fails against unmodified code — a test that is red on correct "
                                  "code is worse than no test",
                        "mutation_score": 0.0, "caught": [], "missed": [], "mutants_total": 0,
                        "output": tail}

            # ---- 2. mutation campaign ----------------------------------------------------
            mutants = mutate.generate(original, function, limit=MAX_MUTANTS)
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

            logger.info("run %s: test_evaluator %s -> %s (score %.2f, %d mutants, %.1fs)",
                        run_id, function, verdict, score, total, elapsed)
            return {"verdict": verdict, "reason": reason, "mutation_score": score,
                    "caught": caught, "missed": missed, "mutants_total": total,
                    "duration_seconds": elapsed, "threshold": min_score}
        except Exception as e:
            return f"Error: {e}"
        finally:
            if sandbox:
                shutil.rmtree(sandbox, ignore_errors=True)

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    """Self-check against a real workspace: a good test, a weak test and a tautology."""
    ws = tempfile.mkdtemp(prefix="sentinel-eval-demo-")
    src = ("def discount(qty, unit, member):\n"
           "    if qty > 10 and member:\n"
           "        return qty * unit * 0.9\n"
           "    return qty * unit\n")
    with open(os.path.join(ws, "shop.py"), "w", encoding="utf-8") as fh:
        fh.write(src)

    tool = TestEvaluatorTool()
    common = {"target_file": "shop.py", "function": "discount", "repo_workspace": ws}

    good = ("from shop import discount\n\n\n"
            "def test_member_bulk_discount():\n"
            "    assert discount(11, 10.0, True) == 99.0\n"
            "    assert discount(10, 10.0, True) == 100.0\n"
            "    assert discount(11, 10.0, False) == 110.0\n")
    r = tool.invoke({**common, "test_source": good}, {})
    assert r["verdict"] == "accepted", r
    assert r["mutation_score"] >= 0.5, r
    assert r["caught"] and r["mutants_total"] > 0

    taut = "def test_nothing():\n    assert True\n"
    r2 = tool.invoke({**common, "test_source": taut}, {})
    assert r2["verdict"] == "rejected" and "tautolog" in r2["reason"], r2
    assert r2["mutants_total"] == 0, "a tautology is rejected before any mutant is run"

    weak = ("from shop import discount\n\n\n"
            "def test_returns_a_number():\n"
            "    assert isinstance(discount(1, 2.0, False), float)\n")
    r3 = tool.invoke({**common, "test_source": weak}, {})
    assert r3["verdict"] == "rejected", r3
    assert r3["mutation_score"] < 0.5, r3

    broken = ("from shop import discount\n\n\n"
              "def test_wrong_expectation():\n"
              "    assert discount(1, 2.0, False) == 999\n")
    r4 = tool.invoke({**common, "test_source": broken}, {})
    assert r4["verdict"] == "rejected" and "unmodified" in r4["reason"], r4

    # the workspace itself is never written to
    assert os.listdir(ws) == ["shop.py"], os.listdir(ws)
    with open(os.path.join(ws, "shop.py"), encoding="utf-8") as fh:
        assert fh.read() == src, "the original source must be untouched"

    shutil.rmtree(ws, ignore_errors=True)
    print("test_evaluator_tool OK")


if __name__ == "__main__":
    demo()
