"""complexity_metrics_tool (04 §5.6, A3) — measured cyclomatic complexity, base-vs-head delta.

The code_quality_agent must never estimate these numbers itself (04 §5.4 instruction 1); this tool
supplies them. For every changed Python/JS/TS function/method in change_profile it computes an
(approximate) McCabe complexity at head and base and the regression (head − base) plus function
length, reading the workspace + refs from Gateway-seeded sly_data. Returns metrics to the agent.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
from typing import Any, Dict, List, Optional, Tuple, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import ts_parse
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.complexity_metrics")

# ponytail: approximate McCabe (relative signal for the delta); good enough without a radon dep.
_BRANCH = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
           ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert)


def _show(repo: str, ref: str, path: str) -> str:
    proc = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                          capture_output=True, encoding="utf-8", errors="replace", check=False)  # not locale (cp1252 crashes on non-Latin1)
    return proc.stdout if proc.returncode == 0 else ""


def _complexity(node: ast.AST) -> int:
    count = 1
    for n in ast.walk(node):
        if isinstance(n, _BRANCH):
            count += 1
        elif isinstance(n, ast.BoolOp):
            count += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            count += 1 + len(n.ifs)
        elif isinstance(n, ast.Match):
            count += len(n.cases)
    return count


class _Funcs(ast.NodeVisitor):
    """qualified name -> function/method node."""
    def __init__(self) -> None:
        self.nodes: Dict[str, ast.AST] = {}
        self._stack: List[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node) -> None:
        qual = ".".join(self._stack + [node.name])
        self.nodes[qual] = node
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def _funcs(src: str) -> Dict[str, ast.AST]:
    if not src:
        return {}
    try:
        v = _Funcs()
        v.visit(ast.parse(src))
        return v.nodes
    except SyntaxError:
        return {}


def _py_metrics(src: str) -> Dict[str, List[int]]:
    """{qualified_name: [complexity, length]} for every python function/method, one parse."""
    out: Dict[str, List[int]] = {}
    for name, node in _funcs(src).items():
        length = getattr(node, "end_lineno", node.lineno) - node.lineno + 1
        out[name] = [_complexity(node), length]
    return out


class ComplexityMetricsTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, base, head, _ = run_inputs(sly_data, args)
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            if not repo:
                return "Error: missing repo_workspace"

            # Per-file metric maps {name: [complexity, length]} at head and base — parse each file
            # ONCE, then look up every changed function. Python uses stdlib ast in-process (safe);
            # JS/TS goes through ts_parse.batch_isolated (tree-sitter's native state leaks and would
            # SEGFAULT the long-lived server on many-file diffs — 04 §5.6).
            todo = []  # (i, f, lang, head_path, base_path, changed_defs)
            for i, f in enumerate(profile.get("files", [])):
                lang = f.get("language")
                if lang not in ("python", "javascript", "typescript") or f.get("change_type") == "deleted":
                    continue
                changed = [d for d in f.get("functions_changed", []) if d["kind"] in ("function", "method")]
                if changed:
                    todo.append((i, f, lang, f["path"], f.get("old_path", f["path"]), changed))

            head_m: Dict[int, Dict[str, List[int]]] = {}
            base_m: Dict[int, Dict[str, List[int]]] = {}
            js = [(i, hp, bp) for i, _f, lang, hp, bp, _c in todo if lang != "python"]
            if js:
                hr = ts_parse.batch_isolated("metrics", [[hp, _show(repo, head, hp)] for _, hp, _ in js])
                br = ts_parse.batch_isolated("metrics", [[bp, _show(repo, base, bp)] for _, _, bp in js])
                for k, (i, _, _) in enumerate(js):
                    head_m[i], base_m[i] = hr[k], br[k]
            for i, _f, lang, hp, bp, _c in todo:
                if lang == "python":
                    head_m[i] = _py_metrics(_show(repo, head, hp)) if head else {}
                    base_m[i] = _py_metrics(_show(repo, base, bp)) if base else {}

            metrics: List[Dict[str, Any]] = []
            for i, f, _lang, _hp, _bp, changed in todo:
                hm, bm = head_m.get(i, {}), base_m.get(i, {})
                for d in changed:
                    name = d["name"]
                    h, b = hm.get(name), bm.get(name)
                    ch, length = (h[0], h[1]) if h else (0, 0)
                    cb = b[0] if b else 0
                    metrics.append({"file": f["path"], "name": name, "complexity_head": ch,
                                    "complexity_base": cb, "complexity_delta": ch - cb, "length": length})

            summary = {
                "functions": metrics,
                "total_functions": len(metrics),
                "max_complexity": max((m["complexity_head"] for m in metrics), default=0),
                "max_delta": max((m["complexity_delta"] for m in metrics), default=0),
            }
            logger.info("run %s: complexity_metrics %d fn, max=%d, max_delta=%d", run_id,
                        summary["total_functions"], summary["max_complexity"], summary["max_delta"])
            return summary
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
