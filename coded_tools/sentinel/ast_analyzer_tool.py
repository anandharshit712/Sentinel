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
                          capture_output=True, encoding="utf-8", errors="replace", check=False)  # not locale (cp1252 crashes on non-Latin1)
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

            # head/base defs per analyzable file. Python parses in-process (stdlib ast, safe); JS/TS
            # goes through ts_parse.batch_isolated — tree-sitter's native state leaks cumulatively and
            # would SEGFAULT the long-lived neuro-san server on a repo with many JS files (04 §5.2).
            head_defs_by_i: Dict[int, Dict[str, Any]] = {}
            base_defs_by_i: Dict[int, Dict[str, Any]] = {}
            js = []  # (i, head_path, base_path)
            for i, f in enumerate(profile["files"]):
                lang = f.get("language")
                if lang not in ("python", "javascript", "typescript") or f.get("change_type") == "deleted":
                    continue
                hp, bp = f["path"], f.get("old_path", f["path"])  # base path follows renames
                if lang == "python":
                    head_defs_by_i[i] = _parse_python(_show(repo, head, hp))
                    base_defs_by_i[i] = _parse_python(_show(repo, base, bp))
                else:
                    js.append((i, hp, bp))
            if js:
                head_res = ts_parse.batch_isolated("defs", [[hp, _show(repo, head, hp)] for _, hp, _ in js])
                base_res = ts_parse.batch_isolated("defs", [[bp, _show(repo, base, bp)] for _, _, bp in js])
                for k, (i, _, _) in enumerate(js):
                    head_defs_by_i[i] = {d["name"]: d for d in head_res[k]}
                    base_defs_by_i[i] = {d["name"]: d for d in base_res[k]}

            new_functions: List[str] = []
            total = 0
            for i, f in enumerate(profile["files"]):
                if i not in head_defs_by_i:
                    continue
                head_defs, base_defs = head_defs_by_i[i], base_defs_by_i[i]
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
