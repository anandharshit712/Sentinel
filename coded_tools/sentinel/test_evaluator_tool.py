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

The experiment itself lives in `lib.evaluate` (no Neuro-SAN dependency, runnable as a CLI); this
tool adapts it to the agent network. Everything happens inside a **copy** of the run workspace,
made per evaluation and deleted after, so neither the source repository nor the original workspace is ever written to. Note what this
still means: the test is code a model wrote, and running it executes that code on this machine,
sandboxed only by a temp directory and a timeout (09 §3).
"""
from __future__ import annotations

from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
# The experiment lives in lib.evaluate so it runs without Neuro-SAN (P5: the standalone evaluator).
# classify / is_tautology are re-exported for existing callers.
from lib.evaluate import DEFAULT_MIN_SCORE, DEFAULT_TEST_PATH, classify, evaluate, is_tautology  # noqa: F401


class TestEvaluatorTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        try:
            target_file = args.get("target_file")
            function = args.get("function")
            test_source = args.get("test_source")
            workspace = args.get("repo_workspace") or sly_data.get("repo_workspace")
            if not (target_file and function and test_source and workspace):
                return "Error: target_file, function, test_source and a repo workspace are required"
            base_sha = (args.get("base_sha")
                        or ((sly_data.get("event") or {}).get("change") or {}).get("base_sha"))
            return evaluate(workspace, target_file, function, test_source,
                            test_path=args.get("test_path") or DEFAULT_TEST_PATH,
                            base_sha=base_sha,
                            min_score=float(args.get("min_score", DEFAULT_MIN_SCORE)))
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    """Self-check against a real workspace: a good test, a weak test and a tautology."""
    import os
    import shutil
    import tempfile

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
