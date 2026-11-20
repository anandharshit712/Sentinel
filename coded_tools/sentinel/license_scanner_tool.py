"""license_scanner_tool (08 §3 P2.5) — deterministic compliance floor + diff snippets.

Feeds `compliance_review_agent` the same two things every reviewer gets: a code-decided floor
(`lib.license_rules` over the in-scope added lines) and the changed code itself, since LLMs cannot
read sly_data.

It first establishes the project's OWN declared licence from the workspace (`package.json`,
`pyproject.toml`, `LICENSE`), because that is what makes a copyleft notice in the diff a conflict
rather than a note.

Dependencies the diff ADDS (requirements*.txt, pyproject.toml PEP 621 arrays, package.json) have
their licence looked up in PyPI / npm (`license_rules.resolve_licenses`: bounded, cached 7 days).
Copyleft becomes one aggregated finding capped at `medium`, so a licence alone never fails the gate.
A dependency that could not be resolved is listed as `unresolved` with the reason and is never
counted as clean. `licenses_seen` reports what was actually inspected.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tomllib
from typing import Any, Dict, List, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from coded_tools.sentinel.dependency_cve_tool import _added_deps
from lib import license_rules, triage
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.license_scanner")

_SNIPPET_CAP = 200
_LLM_FINDINGS_CAP = 50
_DEP_ROWS_CAP = 60
_METADATA = ("package.json", "pyproject.toml", "LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING")


class LicenseScannerTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def _exclude_globs(self, repo: str | None) -> List[str]:
        try:
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}
        defaults = cfg.get("defaults") or {}
        rc = (cfg.get("repos") or {}).get(repo) or {}
        return rc.get("exclude_globs", defaults.get("exclude_globs", [])) or []

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo_ws, _base, _head, _name = run_inputs(sly_data, args)
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            event = sly_data.get("event") or {}
            globs = self._exclude_globs((event.get("repo") or {}).get("name"))

            declared, family = license_rules.project_license(self._metadata_files(repo_ws))

            by_file: Dict[str, Dict[int, str]] = {}
            for path, lineno, content in triage.iter_added_lines(profile):
                # Licence and manifest files are the point here, so they are scanned even when the
                # exclusion globs would normally drop generated/config noise.
                if triage.is_excluded(path, globs) and os.path.basename(path) not in _METADATA:
                    continue
                by_file.setdefault(path, {})[lineno] = content

            findings: List[Dict[str, Any]] = []
            for path, lines in by_file.items():
                text = "\n".join(lines[ln] if ln in lines else ""
                                 for ln in range(1, max(lines) + 1))
                findings.extend(license_rules.scan_file(path, text, lines.keys(), family))

            deps = [("npm" if p.endswith("package.json") else "pypi", name, p)
                    for name, _ver, p in _added_deps(profile)]
            deps += self._pyproject_deps(profile, repo_ws)
            rows = license_rules.resolve_licenses([(eco, name) for eco, name, _p in deps])
            manifest = {(eco, name): p for eco, name, p in deps}
            for r in rows:
                r["manifest"] = manifest[(r["ecosystem"], r["name"])]
            # first, so the per-call cap below can never cut the dependency verdict
            findings[:0] = license_rules.dependency_findings(rows, deps[0][2] if deps else "")
            unresolved = [r for r in rows if r["status"] == "unresolved"]

            snippets = [{"file": p, "line": ln, "code": c}
                        for p, lines in by_file.items() for ln, c in sorted(lines.items())][:_SNIPPET_CAP]

            logger.info("run %s: license_scanner %d finding(s), project licence %r (%s)",
                        run_id, len(findings), declared or "unknown", family)
            return {"findings": findings[:_LLM_FINDINGS_CAP], "findings_total": len(findings),
                    "added_lines": snippets,
                    "project_license": declared or "unknown", "license_family": family,
                    "licenses_seen": ["project declaration", "licence notices in changed files",
                                      "registry licence of each added dependency"],
                    "dependency_licenses": rows[:_DEP_ROWS_CAP], "dependencies_total": len(rows),
                    "note": (f"{len(rows) - len(unresolved)} of {len(rows)} added dependency "
                             f"licence(s) resolved from the registry (latest release)."
                             + (f" {len(unresolved)} UNRESOLVED — not checked, NOT clean: "
                                + "; ".join(f"{r['name']}: {r['reason']}" for r in unresolved[:10])
                                if unresolved else ""))}
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _pyproject_deps(profile: Dict[str, Any], repo_ws: str | None) -> List[tuple]:
        """("pypi", name, path) for PEP 621 dependencies whose line the diff adds.

        An added line alone cannot tell `"requests>=2",` in `dependencies` from `"cli",` in
        `keywords`, so the names come from the parsed file in the workspace and the diff only says
        which of them are new. No workspace -> nothing (missed, never invented). Poetry tables are
        not read.
        """
        out = []
        for f in profile.get("files", []) or []:
            path = f.get("path", "")
            if path.rsplit("/", 1)[-1] != "pyproject.toml" or not repo_ws:
                continue
            try:
                with open(os.path.join(repo_ws, path), "rb") as fh:
                    doc = tomllib.load(fh)
            except (OSError, ValueError):
                continue
            proj = doc.get("project") or {}
            specs = list(proj.get("dependencies") or [])
            groups = list((proj.get("optional-dependencies") or {}).values())
            groups += list((doc.get("dependency-groups") or {}).values())
            for group in groups:
                if isinstance(group, list):
                    specs += group
            declared = {m.group(1).lower() for s in specs if isinstance(s, str)
                        for m in [re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", s)] if m}
            added = " ".join(c for p, _ln, c in triage.iter_added_lines({"files": [f]}))
            out += [("pypi", n, path) for n in sorted(declared)
                    if re.search(r"""["']\s*""" + re.escape(n) + r"(?![A-Za-z0-9._-])", added, re.I)]
        return out

    @staticmethod
    def _metadata_files(repo_ws: str | None) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not repo_ws:
            return out
        for name in _METADATA:
            try:
                with open(os.path.join(repo_ws, name), encoding="utf-8", errors="replace") as fh:
                    out[name] = fh.read(20000)
            except OSError:
                continue
        return out

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        # registry lookups block for up to LOOKUP_TIMEOUT s; keep them off the event loop
        return await asyncio.to_thread(self.invoke, args, sly_data)


def demo() -> None:
    profile = {"files": [{"path": "app/users.py", "hunks": [{
        "new_start": 3,
        "patch": "@@ -1,0 +3,2 @@\n+log.info('signup email=%s', user.email)\n+x = datetime.utcnow()\n",
    }]}]}
    out = LicenseScannerTool().invoke({}, {"change_profile": profile, "run_id": "demo"})
    cats = [f["category"] for f in out["findings"]]
    assert cats == ["pii_exposure", "deprecated_api"], out
    assert out["license_family"] == "unknown", out   # no workspace -> nothing claimed
    print("license_scanner_tool OK")


if __name__ == "__main__":
    demo()
