"""performance_scanner_tool (08 §3 P2.4) — deterministic performance floor + diff snippets.

Feeds `performance_review_agent` exactly what `secret_scanner` feeds a security reviewer:
  * `findings` — the size-independent detection floor from `lib.perf_rules` over every in-scope
    added line. Never depends on the LLM budget (rule 4: the floor is code's word, not the model's).
  * `added_lines` — the changed code itself, because LLMs cannot read sly_data
    (neuro-san `user_guide.md`, "Sly data"), so this is how the diff reaches the reviewer.

Unlike the secret floor, these rules need CONTEXT — `db.query(x)` is fine, and the same call one
indent inside a `for` is an N+1 — so each changed file is read at HEAD from the workspace and the
rules are applied to the added lines within that file's real structure.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import perf_rules, triage
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.performance_scanner")

_SNIPPET_CAP = 200      # added lines handed to the LLM
_LLM_FINDINGS_CAP = 50  # same reasoning as secret_scanner: a big audit would blow the context


class PerformanceScannerTool(CodedTool):
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

            by_file: Dict[str, Dict[int, str]] = {}
            for path, lineno, content in triage.iter_added_lines(profile):
                if triage.is_excluded(path, globs):
                    continue
                by_file.setdefault(path, {})[lineno] = content

            findings: List[Dict[str, Any]] = []
            for path, lines in by_file.items():
                text = self._read_head(repo_ws, path)
                if text is None:
                    # No workspace copy (direct call, deleted file): rebuild a sparse buffer from
                    # the added lines alone. Context-dependent rules simply see depth 0 there, so
                    # this under-reports rather than guessing.
                    size = max(lines)
                    buf = [""] * size
                    for ln, code in lines.items():
                        buf[ln - 1] = code
                    text = "\n".join(buf)
                findings.extend(perf_rules.scan_file(path, text, lines.keys()))

            snippets = [{"file": p, "line": ln, "code": c}
                        for p, lines in by_file.items() for ln, c in sorted(lines.items())][:_SNIPPET_CAP]

            logger.info("run %s: performance_scanner %d finding(s) over %d file(s), %d added line(s)",
                        run_id, len(findings), len(by_file), sum(len(v) for v in by_file.values()))
            return {"findings": findings[:_LLM_FINDINGS_CAP], "findings_total": len(findings),
                    "added_lines": snippets, "files_scanned": len(by_file)}
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _read_head(repo_ws: str | None, path: str) -> str | None:
        if not repo_ws:
            return None
        full = os.path.join(repo_ws, path.replace("/", os.sep))
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    """Self-check with no workspace: the sparse-buffer fallback still finds what it can."""
    profile = {"files": [{"path": "app/orders.py", "hunks": [{
        "new_start": 10,
        "patch": "@@ -1,0 +10,2 @@\n+    for i in ids:\n+        rows.append(db.query(i))\n",
    }]}]}
    out = PerformanceScannerTool().invoke({}, {"change_profile": profile, "run_id": "demo"})
    cats = [f["category"] for f in out["findings"]]
    assert cats == ["n_plus_one"], out
    assert out["added_lines"][0]["line"] == 10, out
    print("performance_scanner_tool OK")


if __name__ == "__main__":
    demo()
