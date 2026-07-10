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


def _complexity_and_length(language: str, path: str, src: str, name: str) -> Optional[Tuple[int, int]]:
    if language == "python":
        node = _funcs(src).get(name)
        if node is None:
            return None
        return _complexity(node), getattr(node, "end_lineno", node.lineno) - node.lineno + 1
    if language in ("javascript", "typescript"):
        try:
            return ts_parse.complexity_and_length(path, src, name)
        except Exception:
            return None  # never let a parse error break the pipeline
    return None


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
                lang = f.get("language")
                if lang not in ("python", "javascript", "typescript") or f.get("change_type") == "deleted":
                    continue
                changed = [d for d in f.get("functions_changed", []) if d["kind"] in ("function", "method")]
                if not changed:
                    continue
                old_path = f.get("old_path", f["path"])
                head_src = _show(repo, head, f["path"]) if head else ""
                base_src = _show(repo, base, old_path) if base else ""
                # ponytail: re-parses the file per changed function (was once-per-file for the pure
                # Python path); fine for the handful of changed functions a diff typically touches,
                # revisit with a per-file parse cache if large multi-function diffs prove slow.
                for d in changed:
                    name = d["name"]
                    hc = _complexity_and_length(lang, f["path"], head_src, name)
                    bc = _complexity_and_length(lang, old_path, base_src, name)
                    ch, length = hc if hc else (0, 0)
                    cb = bc[0] if bc else 0
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
