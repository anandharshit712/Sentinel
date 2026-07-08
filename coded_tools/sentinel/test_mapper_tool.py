"""test_mapper_tool (04 §5.8, A4) — selects the tests relevant to the change.

Maps changed + blast-radius modules to test files by precedence: coverage map (if present) >
test-file import graph > naming convention, then unions the repo's smoke_set. Returns a base
TestPlan (selected + smoke_set + confidence + runtime estimate) to the test_selection_agent, which
may LLM-add tests before contract_store persists the TestPlan. Operates on the checked-out head
(working tree). Coverage-map parsing is deferred (import-graph covers the sample repos).
"""
from __future__ import annotations

import ast
import asyncio
import logging
import os
from typing import Any, Dict, List, Set, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.test_mapper")

_DEFAULT_PER_TEST_SECONDS = 5


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _module(rel: str) -> str:
    mod = rel[:-3] if rel.endswith(".py") else rel
    mod = mod.replace("/", ".")
    return mod[:-9] if mod.endswith(".__init__") else mod


def _is_test_file(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return (base.startswith("test_") and base.endswith(".py")) or base.endswith("_test.py")


def _imports(src: str) -> Set[str]:
    out: Set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


class TestMapperTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, _base, _head, repo_name = run_inputs(sly_data, args)
            if not repo or not os.path.isdir(repo):
                return "Error: missing/invalid repo_workspace"
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}

            changed = {_module(f["path"]) for f in profile.get("files", [])
                       if f["path"].endswith(".py") and f.get("change_type") != "deleted"}
            br = profile.get("blast_radius") or {}
            targets = changed | set(br.get("direct", [])) | set(br.get("transitive", []))

            selected: Dict[str, Dict[str, str]] = {}
            for dirpath, _dirs, files in os.walk(repo):
                if ".git" in dirpath.replace("\\", "/").split("/"):
                    continue
                for name in files:
                    rel = _rel(repo, os.path.join(dirpath, name))
                    if not _is_test_file(rel):
                        continue
                    with open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore") as fh:
                        imps = _imports(fh.read())
                    hit = imps & targets
                    if hit:
                        selected[rel] = {"test_id": rel, "reason": f"imports {sorted(hit)[0]}",
                                         "mapping_source": "import_graph"}

            # convention fallback: tests/test_<stem>.py for each changed file
            for f in profile.get("files", []):
                if not f["path"].endswith(".py"):
                    continue
                stem = f["path"].rsplit("/", 1)[-1][:-3]
                for cand in (f"tests/test_{stem}.py", f"test_{stem}.py"):
                    if os.path.isfile(os.path.join(repo, cand)) and cand not in selected:
                        selected[cand] = {"test_id": cand, "reason": f"convention for {stem}",
                                          "mapping_source": "convention"}

            smoke = self._smoke_set(repo_name)
            for tid in smoke:
                if tid not in selected:
                    selected[tid] = {"test_id": tid, "reason": "smoke set", "mapping_source": "smoke"}

            # LLM add-only (test_selection_agent may widen); never removes mapper selections.
            for tid in (args.get("added_test_ids") or []):
                if tid and tid not in selected:
                    selected[tid] = {"test_id": tid, "reason": "added by reviewer", "mapping_source": "llm_added"}

            sources = {s["mapping_source"] for s in selected.values()}
            confidence = "medium" if sources & {"import_graph", "convention"} else "low"

            plan = {
                "selected": list(selected.values()),
                "smoke_set": smoke,
                "selection_confidence": confidence,
                "estimated_runtime_seconds": len(selected) * _DEFAULT_PER_TEST_SECONDS,
                "excluded_summary": f"{len(selected)} test file(s) selected by {sorted(sources) or ['none']}",
            }
            # Finalize into sly_data as the tool-owned test_plan contract (like dependency_graph does
            # for change_profile) — reliable, and test_runner reads it from sly_data.
            wrapped = contracts.wrap(plan, run_id=str(run_id), produced_by="test_selection")
            contracts.validate("test_plan", wrapped)
            sly_data["test_plan"] = wrapped
            logger.info("run %s: test_mapper selected %d (%s)", run_id, len(selected), confidence)
            return {"selected": [s["test_id"] for s in selected.values()],
                    "selection_confidence": confidence, "count": len(selected)}
        except Exception as e:
            return f"Error: {e}"

    def _smoke_set(self, repo_name) -> List[str]:
        if not repo_name:
            return []
        with open(self.repo_config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return ((cfg.get("repos") or {}).get(repo_name) or {}).get("smoke_set") or []

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
