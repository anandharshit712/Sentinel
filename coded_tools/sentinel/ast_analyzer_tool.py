"""ast_analyzer_tool (04 §5.2, A1) — changed/new functions and classes per changed file.

Reads change_profile_wip (from git_diff), parses each Python/JS/TS file at base and head, and
marks each def whose line range overlaps a changed hunk as functions_changed (is_new when absent
at base). Mutates change_profile_wip in place. Python uses stdlib `ast`; JS/TS uses tree-sitter
(lib/ts_parse). Other languages are left with no functions_changed.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import ts_parse
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.ast_analyzer")


def _show(repo: str, ref: str, path: str) -> str:
    proc = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                          capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else ""  # absent at ref (e.g. added file)


class _Defs(ast.NodeVisitor):
    def __init__(self) -> None:
        self.defs: List[Dict[str, Any]] = []
        self._stack: List[tuple] = []  # (kind, name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node, "method" if self._in_class() else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def _in_class(self) -> bool:
        return bool(self._stack) and self._stack[-1][0] == "class"

    def _record(self, node: ast.AST, kind: str) -> None:
        qual = ".".join(n for _, n in self._stack + [(kind, node.name)])
        self.defs.append({"name": qual, "kind": kind, "line_start": node.lineno,
                          "line_end": getattr(node, "end_lineno", node.lineno)})
        self._stack.append((kind, node.name))
        self.generic_visit(node)
        self._stack.pop()


def _parse_python(src: str) -> Dict[str, Dict[str, Any]]:
    if not src:
        return {}
    try:
        v = _Defs()
        v.visit(ast.parse(src))
        return {d["name"]: d for d in v.defs}
    except SyntaxError:
        return {}


def _parse(language: str, path: str, src: str) -> Dict[str, Dict[str, Any]]:
    if language == "python":
        return _parse_python(src)
    if language in ("javascript", "typescript"):
        try:
            return {d["name"]: d for d in ts_parse.extract_defs(path, src)}
        except Exception:
            return {}  # never let a parse error break the pipeline
    return {}


def _changed_ranges(hunks: List[Dict[str, Any]]) -> List[tuple]:
    out = []
    for h in hunks:
        start = h.get("new_start", 0)
        length = max(h.get("new_lines", 0), 1)
        out.append((start, start + length - 1))
    return out


def _overlaps(d: Dict[str, Any], ranges: List[tuple]) -> bool:
    lo, hi = d["line_start"], d["line_end"]
    return any(lo <= r_hi and r_lo <= hi for r_lo, r_hi in ranges)


class AstAnalyzerTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, base, head, _ = run_inputs(sly_data, args)
            profile = sly_data.get("change_profile_wip")
            if not profile:
                return "Error: change_profile_wip missing (run git_diff first)"
            if not (repo and base and head):
                return "Error: missing repo_workspace/base_sha/head_sha"

            new_functions: List[str] = []
            total = 0
            for f in profile["files"]:
                lang = f.get("language")
                if lang not in ("python", "javascript", "typescript") or f.get("change_type") == "deleted":
                    continue
                head_defs = _parse(lang, f["path"], _show(repo, head, f["path"]))
                base_defs = _parse(lang, f.get("old_path", f["path"]),
                                   _show(repo, base, f.get("old_path", f["path"])))  # follow renames
                ranges = _changed_ranges(f.get("hunks", []))
                # a fully-added file has no base and (with -U0) may have no hunks: treat all defs as changed
                treat_all = not base_defs or f.get("change_type") == "added"
                changed = []
                for name, d in head_defs.items():
                    if treat_all or _overlaps(d, ranges):
                        is_new = name not in base_defs
                        changed.append({**d, "is_new": is_new})
                        if is_new and d["kind"] in ("function", "method"):
                            new_functions.append(f"{f['path']}::{name}")
                f["functions_changed"] = changed
                total += len(changed)

            profile["new_functions"] = new_functions
            logger.info("run %s: ast_analyzer %d changed defs, %d new", run_id, total, len(new_functions))
            return {"functions_changed": total, "new_functions": new_functions}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
