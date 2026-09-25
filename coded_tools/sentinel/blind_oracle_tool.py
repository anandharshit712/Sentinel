"""blind_oracle_tool (11 §3 Tier 2, O5) — what SHOULD this function return?

The general answer to the question mutation testing cannot ask. Tier 3 (`lib.spec_check`) catches
the numeric form of a contradicted docstring deterministically; this tier handles the rest — a
docstring describing behaviour in prose, a PR stating an intent, callers assuming something the
implementation does not do.

**The mechanism the tier lives or dies by: the agent never sees the function body.** A model shown
the implementation restates it, which is the documented failure of LLM-generated oracles
([arXiv 2410.21136]) and precisely how a test asserting a bug earned a 0.83. This tool assembles
the intent side — signature, docstring, types, PR text, call sites, and the module with the
target's body replaced by `...` — and nothing else. `lib.intent.redact_body` is the safety
mechanism, and this tool refuses to answer if redaction did not remove the body.

The agent replies with expected input/output cases. `_compare` then runs the REAL function against
those inputs and reports disagreements. The model never sees the outcome and never grades itself:
it states an expectation, and code checks it.

A disagreement means the implementation and the stated intent differ — not which one is wrong. The
docstring may be stale. That call belongs to a human.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import intent as intent_mod
from lib.pyexec import fresh_import_env

logger = logging.getLogger("coded_tools.blind_oracle")

PROBE_TIMEOUT = 60
MAX_CASES = 8


class BlindOracleTool(CodedTool):
    """Two modes, chosen by whether `expectations` was supplied.

    Without it: return the redacted intent context for the agent to reason over.
    With it: run the real function against the agent's expected cases and report disagreements.
    """

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            target_file = args.get("target_file")
            function = args.get("function")
            repo = args.get("repo_workspace") or sly_data.get("repo_workspace")
            if not (target_file and function and repo):
                return "Error: target_file, function and a repo workspace are required"

            try:
                with open(os.path.join(repo, target_file.replace("/", os.sep)),
                          encoding="utf-8") as fh:
                    source = fh.read()
            except OSError as e:
                return f"Error: cannot read {target_file}: {e}"

            expectations = args.get("expectations")
            if not expectations:
                return self._context(source, function, sly_data, repo, target_file)
            return self._compare(repo, target_file, function, expectations, run_id)
        except Exception as e:
            return f"Error: {e}"

    # ---------------------------------------------------------------- intent side
    def _context(self, source: str, function: str, sly_data: Dict[str, Any],
                 repo: str, target_file: str) -> Union[Dict[str, Any], str]:
        others: Dict[str, str] = {}
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in
                       {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}]
            for name in files:
                if not name.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(root, name), repo).replace(os.sep, "/")
                if rel != target_file:
                    try:
                        with open(os.path.join(root, name), encoding="utf-8",
                                  errors="replace") as fh:
                            others[rel] = fh.read()[:4000]
                    except OSError:
                        pass
            if len(others) >= 20:
                break

        ctx = intent_mod.collect(source, function, sly_data.get("event"), others)

        # The guard. If redaction failed, this tier is worthless while still looking confident —
        # refuse rather than hand the implementation to the model.
        redacted = ctx.get("module_without_body") or ""
        tail = source.split(f"def {function}", 1)[-1][:200]
        if redacted and tail and tail in redacted:
            return ("Error: redaction failed — refusing to show the implementation to the blind "
                    "oracle, because an oracle that has seen the code only restates it")

        if not ctx["has_stated_intent"]:
            return {"has_stated_intent": False, "signature": ctx["signature"],
                    "reason": (f"{function} has no docstring and the change carries no description, "
                               f"so there is nothing to compare the implementation against")}
        return ctx

    # ---------------------------------------------------------------- code side
    def _compare(self, repo: str, target_file: str, function: str,
                 expectations: Any, run_id: str) -> Dict[str, Any]:
        if isinstance(expectations, str):
            try:
                expectations = json.loads(expectations)
            except ValueError as e:
                return {"checked": 0, "agreements": [], "disagreements": [],
                        "reason": f"expectations must be JSON: {e}"}
        cases = [c for c in (expectations or []) if isinstance(c, dict)][:MAX_CASES]
        if not cases:
            return {"checked": 0, "agreements": [], "disagreements": [],
                    "reason": "no usable expected cases were supplied"}

        module = target_file[:-3].replace("/", ".") if target_file.endswith(".py") else target_file
        # A probe script in a subprocess with a timeout, not an in-process import: this is
        # repository code, and it gets the same treatment as the test runner gives it.
        probe = "\n".join([
            "import json, sys",
            f"from {module} import {function} as _f",
            "out = []",
            "for c in json.loads(sys.argv[1]):",
            "    try:",
            "        out.append({'actual': _f(*c.get('args', []), **c.get('kwargs', {}))})",
            "    except Exception as e:",
            "        out.append({'actual': type(e).__name__ + ': ' + str(e)})",
            "print(json.dumps(out, default=str))",
            "",
        ])
        fd, path = tempfile.mkstemp(suffix=".py", prefix="sentinel_probe_", dir=repo)
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(probe)
            r = subprocess.run([sys.executable, os.path.basename(path), json.dumps(cases)],
                               cwd=repo, capture_output=True, encoding="utf-8", errors="replace",
                               timeout=PROBE_TIMEOUT, check=False, env=fresh_import_env())
            if r.returncode != 0:
                return {"checked": 0, "agreements": [], "disagreements": [],
                        "reason": f"could not call {function}: {(r.stderr or '')[-200:]}"}
            actuals = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {"checked": 0, "agreements": [], "disagreements": [],
                    "reason": f"probe failed: {e}"}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        agreements, disagreements = [], []
        for case, got in zip(cases, actuals):
            expected, actual = case.get("expected"), got.get("actual")
            same = str(expected) == str(actual)
            if not same and isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                same = abs(float(expected) - float(actual)) <= 1e-9
            entry = {"args": case.get("args", []), "expected": expected, "actual": actual,
                     "because": case.get("because", "")}
            (agreements if same else disagreements).append(entry)

        logger.info("run %s: blind_oracle %s — %d agree, %d disagree",
                    run_id, function, len(agreements), len(disagreements))
        return {
            "checked": len(cases), "agreements": agreements, "disagreements": disagreements,
            "verdict": "disputed" if disagreements else "confirms_intent",
            "caveat": ("A disagreement means the implementation and the stated intent differ, not "
                       "which one is wrong — the documentation may be stale."),
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


BUGGY_SRC = (
    'def loyalty_discount(total, years):\n'
    '    """Members of 3+ years get 15% off. Everyone else pays full price."""\n'
    '    if years >= 3:\n'
    '        return round(total * 0.90, 2)\n'
    '    return total\n'
)


def demo() -> None:
    import shutil

    ws = tempfile.mkdtemp(prefix="blind-demo-")
    with open(os.path.join(ws, "pricing.py"), "w", encoding="utf-8") as fh:
        fh.write(BUGGY_SRC)
    tool = BlindOracleTool()

    # intent side: the docstring reaches the oracle, the arithmetic does not
    ctx = tool.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                       "repo_workspace": ws}, {})
    assert ctx["has_stated_intent"] is True, ctx
    assert "15%" in ctx["docstring"]
    assert "0.90" not in ctx["module_without_body"], "the body must never reach the oracle"

    # code side: an expectation derived from "15% off" disagrees with what the code does
    res = tool.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                       "repo_workspace": ws,
                       "expectations": [{"args": [100.0, 3], "expected": 85.0,
                                         "because": "the docstring promises 15% off"},
                                        {"args": [100.0, 1], "expected": 100.0,
                                         "because": "under 3 years pays full price"}]}, {})
    assert res["verdict"] == "disputed", res
    assert len(res["disagreements"]) == 1 and res["disagreements"][0]["actual"] == 90.0, res
    assert len(res["agreements"]) == 1, res

    # an implementation matching its documentation confirms it
    with open(os.path.join(ws, "pricing.py"), "w", encoding="utf-8") as fh:
        fh.write(BUGGY_SRC.replace("0.90", "0.85"))
    ok = tool.invoke({"target_file": "pricing.py", "function": "loyalty_discount",
                      "repo_workspace": ws,
                      "expectations": [{"args": [100.0, 3], "expected": 85.0}]}, {})
    assert ok["verdict"] == "confirms_intent", ok

    # nothing documented: the tier says so rather than inventing an expectation
    with open(os.path.join(ws, "bare.py"), "w", encoding="utf-8") as fh:
        fh.write("def f(x):\n    return x * 3\n")
    bare = tool.invoke({"target_file": "bare.py", "function": "f", "repo_workspace": ws}, {})
    assert bare["has_stated_intent"] is False, bare

    shutil.rmtree(ws, ignore_errors=True)
    print("blind_oracle_tool OK")


if __name__ == "__main__":
    demo()
