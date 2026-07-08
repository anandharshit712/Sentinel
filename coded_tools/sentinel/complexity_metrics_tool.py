"""complexity_metrics_tool (04 §5.6, A3) — measured cyclomatic complexity, base-vs-head delta.

The code_quality_agent must never estimate these numbers itself (04 §5.4 instruction 1); this tool
supplies them. For every changed Python function/method in change_profile it computes an
(approximate) McCabe complexity at head and base and the regression (head − base) plus function
length, reading the workspace + refs from Gateway-seeded sly_data. Returns metrics to the agent.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.complexity_metrics")

# ponytail: approximate McCabe (relative signal for the delta); good enough without a radon dep.
_BRANCH = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
           ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert)


def _show(repo: str, ref: str, path: str) -> str:
    proc = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                          capture_output=True, text=True, check=False)
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


class ComplexityMetricsTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, base, head, _ = run_inputs(sly_data, args)
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            if not repo:
                return "Error: missing repo_workspace"

            metrics: List[Dict[str, Any]] = []
            for f in profile.get("files", []):
                if f.get("language") != "python" or f.get("change_type") == "deleted":
                    continue
                changed = [d for d in f.get("functions_changed", []) if d["kind"] in ("function", "method")]
                if not changed:
                    continue
                head_fns = _funcs(_show(repo, head, f["path"])) if head else {}
                base_fns = _funcs(_show(repo, base, f.get("old_path", f["path"]))) if base else {}
                for d in changed:
                    name = d["name"]
                    hn = head_fns.get(name)
                    ch = _complexity(hn) if hn else 0
                    bn = base_fns.get(name)
                    cb = _complexity(bn) if bn else 0
                    length = (getattr(hn, "end_lineno", hn.lineno) - hn.lineno + 1) if hn else 0
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
