"""gap_context_tool (09 §4 P3.7) — hands the generation agent everything it needs to write a test.

An LLM cannot read sly_data (neuro-san `user_guide.md`, "Sly data"), and it cannot read the
repository either. So the one generative step in this project gets its inputs through a tool
return: the function's real source, which of its lines no test executes, and two of the project's
existing tests.

The existing tests matter as much as the gap. A generated test that ignores the project's fixtures,
import style and naming conventions is one a reviewer has to rewrite before adopting, which defeats
the point. Style is shown, not described.

Read-only: it opens files in the run workspace and writes nothing anywhere.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.gap_context")

MAX_EXAMPLE_CHARS = 2500     # two example tests, trimmed — style needs a sample, not a corpus
MAX_SOURCE_CHARS = 6000


def _find_example_tests(repo: str, limit: int = 2) -> List[Dict[str, str]]:
    """A couple of the project's own test files, smallest first — small ones read as templates."""
    found: List[tuple[int, str]] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in
                   {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", "dist"}]
        for name in files:
            if name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py"):
                path = os.path.join(root, name)
                try:
                    found.append((os.path.getsize(path), path))
                except OSError:
                    continue
    out: List[Dict[str, str]] = []
    for _size, path in sorted(found)[:limit]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                out.append({"path": os.path.relpath(path, repo).replace(os.sep, "/"),
                            "source": fh.read()[:MAX_EXAMPLE_CHARS]})
        except OSError:
            continue
    return out


class GapContextTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo = args.get("repo_workspace") or sly_data.get("repo_workspace")
            if not repo:
                return "Error: no repo workspace for this run"

            gaps_contract = sly_data.get("coverage_gaps") or {}
            gaps = [g for g in (gaps_contract.get("gaps") or []) if g.get("status") != "covered"]
            if not gaps:
                measured = gaps_contract.get("measured")
                return {"gap": None,
                        "reason": ("no coverage was measured for this run, so there is nothing to "
                                   "target" if measured is False else
                                   "every changed function is already covered")}

            # Gaps arrive ranked by coverage_gap (sensitive code, then new code, then size).
            index = int(args.get("gap_index", 0))
            if index >= len(gaps):
                return {"gap": None, "reason": f"only {len(gaps)} gap(s) available"}
            gap = gaps[index]

            abs_path = os.path.join(repo, gap["file"].replace("/", os.sep))
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError as e:
                return f"Error: cannot read {gap['file']}: {e}"

            start = max(1, int(gap.get("line_start", 1)))
            end = min(len(lines), int(gap.get("line_end", len(lines))))
            source = "\n".join(lines[start - 1:end])[:MAX_SOURCE_CHARS]
            uncovered = gap.get("uncovered_lines") or []
            uncovered_src = [{"line": ln, "code": lines[ln - 1]}
                             for ln in uncovered if 1 <= ln <= len(lines)]

            # The import path a test would use, derived from the file path.
            module = gap["file"][:-3].replace("/", ".") if gap["file"].endswith(".py") else gap["file"]

            logger.info("run %s: gap_context -> %s::%s (%d uncovered line(s))",
                        run_id, gap["file"], gap.get("function"), len(uncovered))
            return {
                "gap": {"file": gap["file"], "function": gap.get("function"),
                        "status": gap.get("status"), "line_start": start, "line_end": end,
                        "priority": gap.get("priority"), "sensitive": gap.get("sensitive")},
                "module_path": module,
                "function_source": source,
                "uncovered_lines": uncovered_src,
                "example_tests": _find_example_tests(repo),
                "gaps_remaining": len(gaps) - index - 1,
            }
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    import tempfile

    ws = tempfile.mkdtemp(prefix="gapctx-")
    os.makedirs(os.path.join(ws, "tests"), exist_ok=True)
    with open(os.path.join(ws, "shop.py"), "w", encoding="utf-8") as fh:
        fh.write("def discount(qty, unit, member):\n"
                 "    if qty > 10 and member:\n"
                 "        return qty * unit * 0.9\n"
                 "    return qty * unit\n")
    with open(os.path.join(ws, "tests", "test_existing.py"), "w", encoding="utf-8") as fh:
        fh.write("from shop import discount\n\n\ndef test_plain():\n    assert discount(1, 2.0, False) == 2.0\n")

    sly = {"run_id": "d", "repo_workspace": ws, "coverage_gaps": {"measured": True, "gaps": [
        {"file": "shop.py", "function": "discount", "status": "partial", "line_start": 1,
         "line_end": 4, "uncovered_lines": [3], "priority": 3.1, "sensitive": False}]}}
    out = GapContextTool().invoke({}, sly)
    assert out["gap"]["function"] == "discount", out
    assert "qty > 10" in out["function_source"]
    assert out["uncovered_lines"] == [{"line": 3, "code": "        return qty * unit * 0.9"}], out
    assert out["module_path"] == "shop"
    assert out["example_tests"] and "test_plain" in out["example_tests"][0]["source"]

    # covered-only and unmeasured runs say so rather than inventing a target
    assert GapContextTool().invoke({}, {"run_id": "d", "repo_workspace": ws,
                                        "coverage_gaps": {"measured": True, "gaps": []}})["gap"] is None
    unmeasured = GapContextTool().invoke({}, {"run_id": "d", "repo_workspace": ws,
                                              "coverage_gaps": {"measured": False, "gaps": []}})
    assert unmeasured["gap"] is None and "no coverage" in unmeasured["reason"]

    print("gap_context_tool OK")


if __name__ == "__main__":
    demo()
