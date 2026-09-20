"""license_scanner_tool (08 §3 P2.5) — deterministic compliance floor + diff snippets.

Feeds `compliance_review_agent` the same two things every reviewer gets: a code-decided floor
(`lib.license_rules` over the in-scope added lines) and the changed code itself, since LLMs cannot
read sly_data.

It first establishes the project's OWN declared licence from the workspace (`package.json`,
`pyproject.toml`, `LICENSE`), because that is what makes a copyleft notice in the diff a conflict
rather than a note. `licenses_seen` reports what was actually inspected — the tool does not resolve
each dependency's licence, which needs a registry lookup per dependency and is out of scope for
this phase (08 §3 P2.3). Saying so beats implying a dependency audit that did not happen.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import license_rules, triage
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.license_scanner")

_SNIPPET_CAP = 200
_LLM_FINDINGS_CAP = 50
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

            snippets = [{"file": p, "line": ln, "code": c}
                        for p, lines in by_file.items() for ln, c in sorted(lines.items())][:_SNIPPET_CAP]

            logger.info("run %s: license_scanner %d finding(s), project licence %r (%s)",
                        run_id, len(findings), declared or "unknown", family)
            return {"findings": findings[:_LLM_FINDINGS_CAP], "findings_total": len(findings),
                    "added_lines": snippets,
                    "project_license": declared or "unknown", "license_family": family,
                    "licenses_seen": ["project declaration", "licence notices in changed files"],
                    "note": "Dependency licences are NOT resolved (needs a registry lookup per "
                            "dependency); this is the project's own licence plus notices in the diff."}
        except Exception as e:
            return f"Error: {e}"

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
        return self.invoke(args, sly_data)


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
