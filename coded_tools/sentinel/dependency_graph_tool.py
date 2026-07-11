"""dependency_graph_tool (04 §5.3, A1) — blast radius + sensitive flags; finalizes ChangeProfile.

Builds the Python (dotted-module) and JS/TS (path-based) import graphs at head, computes the
dependents (direct + transitive) of the changed modules, applies the repo's sensitive_rules (path
globs + symbol regexes), then assembles the full ChangeProfile from change_profile_wip, validates
it and writes it to sly_data as the authoritative `change_profile` contract (the finalize step of
the change-analysis pipeline).
"""
from __future__ import annotations

import ast
import asyncio
import fnmatch
import logging
import re
import subprocess
from typing import Any, Dict, List, Set, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts, js_imports
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.dependency_graph")


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          encoding="utf-8", errors="replace", check=False)  # not locale (cp1252 crashes on non-Latin1)
    return proc.stdout if proc.returncode == 0 else ""


def _module_of(path: str) -> str:
    mod = path[:-3] if path.endswith(".py") else path
    mod = mod.replace("/", ".").replace("\\", ".")
    return mod[:-9] if mod.endswith(".__init__") else mod


def _imports(src: str, known: Set[str]) -> Set[str]:
    """Internal modules imported by src (resolved against the set of known modules)."""
    out: Set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.update(m for m in known if a.name == m or a.name.startswith(m + "."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # ponytail: relative imports (level>0) not resolved; the sample repos use absolute imports.
            for a in node.names:
                cand = f"{node.module}.{a.name}"
                out.update(m for m in known if m in (node.module, cand))
    return out


class DependencyGraphTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, _base, head, repo_name = run_inputs(sly_data, args)
            head = head or "HEAD"
            profile = sly_data.get("change_profile_wip")
            if not profile:
                return "Error: change_profile_wip missing (run git_diff first)"
            if not repo:
                return "Error: missing repo_workspace"

            all_files = _git(repo, "ls-tree", "-r", "--name-only", head).splitlines()
            py_files = [p for p in all_files if p.endswith(".py")]
            js_files = [p for p in all_files if p.endswith(js_imports.JS_EXTS)]

            known = {_module_of(p) for p in py_files}
            dependents: Dict[str, Set[str]] = {m: set() for m in known}
            for p in py_files:
                importer = _module_of(p)
                for dep in _imports(_git(repo, "show", f"{head}:{p}"), known):
                    if dep != importer:
                        dependents.setdefault(dep, set()).add(importer)

            known_js = {js_imports.module_key(p) for p in js_files}
            dependents.update({m: set() for m in known_js})
            for p in js_files:
                importer = js_imports.module_key(p)
                for dep in js_imports.extract_import_targets(
                        _git(repo, "show", f"{head}:{p}"), importer, known_js):
                    if dep != importer:
                        dependents.setdefault(dep, set()).add(importer)

            changed_files = [f for f in profile["files"] if f.get("change_type") != "deleted"]
            changed_mods = set()
            for f in changed_files:
                if f["path"].endswith(".py"):
                    changed_mods.add(_module_of(f["path"]))
                elif f["path"].endswith(js_imports.JS_EXTS):
                    changed_mods.add(js_imports.module_key(f["path"]))

            direct: Set[str] = set()
            for m in changed_mods:
                direct |= dependents.get(m, set())
            direct -= changed_mods

            transitive: Set[str] = set()
            frontier = set(direct)
            while frontier:
                nxt = set()
                for m in frontier:
                    nxt |= dependents.get(m, set())
                nxt -= changed_mods | direct | transitive
                transitive |= nxt
                frontier = nxt

            profile["blast_radius"] = {
                "direct": sorted(direct),
                "transitive": sorted(transitive),
                "count": len(direct | transitive),
            }
            flags = self._sensitive_flags(repo_name, changed_files)
            # merge the LLM's add-only flags (§5.3); LLM can add, never remove detected flags
            valid_flags = {"auth", "payments", "data_deletion", "migration", "public_api"}
            known = {f["flag"] for f in flags}
            for extra in (args.get("added_flags") or []):
                if extra in valid_flags and extra not in known:
                    flags.append({"flag": extra, "matched_by": "llm", "files": []})
                    known.add(extra)
            profile["sensitive_flags"] = flags
            # LLM may refine classification (heuristic is the default)
            if args.get("classification"):
                profile["classification"] = args["classification"]

            wrapped = contracts.wrap(profile, run_id=str(run_id), produced_by="dependency_graph")
            contracts.validate("change_profile", wrapped)
            sly_data["change_profile"] = wrapped
            sly_data.pop("change_profile_wip", None)
            logger.info("run %s: dependency_graph blast=%d sensitive=%d", run_id,
                        profile["blast_radius"]["count"], len(profile["sensitive_flags"]))
            return wrapped
        except Exception as e:
            return f"Error: {e}"

    def _sensitive_flags(self, repo_name, changed_files) -> List[Dict[str, Any]]:
        if not repo_name:
            return []
        with open(self.repo_config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        rules = ((cfg.get("repos") or {}).get(repo_name) or {}).get("sensitive_rules") or []
        flags: List[Dict[str, Any]] = []
        for rule in rules:
            globs = rule.get("path_globs", [])
            regexes = [re.compile(r) for r in rule.get("symbol_regexes", [])]
            hit: Set[str] = set()
            for f in changed_files:
                path = f["path"]
                if any(fnmatch.fnmatch(path, g) for g in globs):
                    hit.add(path)
                symbols = [d["name"] for d in f.get("functions_changed", [])]
                if any(rx.search(s) for rx in regexes for s in symbols):
                    hit.add(path)
            if hit:
                flags.append({"flag": rule["flag"], "matched_by": rule["flag"], "files": sorted(hit)})
        return flags

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
