"""secret_scanner_tool (04 §5.4, A2) — scans added diff lines for secrets + dangerous sinks.

Reads `change_profile` from sly_data and, when a `review_plan` exists, its shard's file set. It
returns two things to the calling `security_reviewer_n`:
  * `findings` — deterministic, size-independent DETECTION FLOOR: hardcoded-secret rules (regex +
    entropy) AND dangerous-sink rules (`lib/triage.SINK_RULES`) over EVERY in-scope added line.
    This never depends on the LLM budget or repo size (04 §5.18).
  * `review_snippets` — the top-ranked added lines within the per-reviewer budget (`triage.rank`),
    each with `why_flagged` + surrounding context, so the LLM spends its attention on the riskiest
    lines rather than the first N by file-walk order.

LLMs can't see sly_data (user_guide "Sly data"), so the snippets are how the diff reaches the
security LLM. With no `review_plan` (legacy / direct-call / PR without planner) it falls back to
scanning the whole non-excluded diff, ranked to a 300-line cap — same return shape as before.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import triage

logger = logging.getLogger("coded_tools.secret_scanner")

_LEGACY_CAP = 300  # no-plan fallback: ranked top-N lines handed to the LLM

# (category, regex, cwe, title) — specific rules first; one secret finding per line, first match wins.
_RULES: List[Tuple[str, "re.Pattern", str, str]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}"), "CWE-798", "AWS access key ID"),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
     "CWE-321", "Private key committed in source"),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "CWE-522", "JSON Web Token"),
    ("hardcoded_credential",
     re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
                r"\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
     "CWE-798", "Hardcoded credential"),
]
_QUOTED = re.compile(r"['\"]([^'\"]{20,})['\"]")


class SecretScannerTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def _cfg(self, repo: Optional[str]) -> Dict[str, Any]:
        """Merged config for `repo` (defaults anchor + per-repo overrides)."""
        try:
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}
        defaults = cfg.get("defaults") or {}
        rc = (cfg.get("repos") or {}).get(repo) or {}
        return {
            "budget": rc.get("review_budget_lines", defaults.get("review_budget_lines", 800)),
            "exclude_globs": rc.get("exclude_globs", defaults.get("exclude_globs", [])) or [],
        }

    def _secret(self, content: str) -> Optional[Tuple[str, str, str]]:
        """(category, cwe, title) for the first secret rule / entropy hit, else None."""
        for cat, rx, cwe, title in _RULES:
            if rx.search(content):
                return cat, cwe, title
        m = _QUOTED.search(content)
        if m and triage.entropy(m.group(1)) >= 4.0:
            return "high_entropy_secret", "CWE-798", "High-entropy secret string"
        return None

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            event = sly_data.get("event") or {}
            repo = (event.get("repo") or {}).get("name")
            cfg = self._cfg(repo)
            extra_globs = cfg["exclude_globs"]

            sensitive_files = set()
            for sf in profile.get("sensitive_flags", []):
                sensitive_files.update(sf.get("files", []))

            plan = sly_data.get("review_plan") or {}
            shard = args.get("shard")

            # ---- determine in-scope added lines ----
            if plan and shard is not None:
                shard = int(shard)
                shard_files = self._shard_files(plan, shard)
                if shard_files is None:
                    logger.info("run %s: secret_scanner shard %s not in plan", run_id, shard)
                    return {"findings": [], "review_snippets": [], "note": f"shard {shard} not in plan"}
                scoped = [(p, ln, c) for (p, ln, c) in triage.iter_added_lines(profile)
                          if p in shard_files]
                budget = cfg["budget"]
            else:
                scoped = [(p, ln, c) for (p, ln, c) in triage.iter_added_lines(profile)
                          if not triage.is_excluded(p, extra_globs)]
                budget = _LEGACY_CAP

            # ---- detection floor: secrets + sinks over ALL in-scope lines ----
            findings: List[Dict[str, Any]] = []
            tag = shard if shard is not None else 0
            n = 0
            for path, lineno, content in scoped:
                sec = self._secret(content)
                if sec:
                    n += 1
                    cat, cwe, title = sec
                    findings.append(self._finding(f"SEC{tag}-{n:03d}", cat, "critical", path, lineno,
                                                   cwe, title, f"{title} detected in an added line of {path}.",
                                                   "Remove the secret from source and rotate it; load from a secrets manager or env var."))
                    continue  # a secret line isn't also sink-scanned
                sink = triage.scan_sink(content)
                if sink:
                    n += 1
                    cat, cwe, sev, title = sink
                    findings.append(self._finding(f"SEC{tag}-SNK-{n:03d}", cat, sev, path, lineno,
                                                   cwe, title, f"{title} on an added line of {path}.",
                                                   "Use a safe API: parameterized queries, no shell=True, safe deserialization, strong hashing."))

            # ---- ranked snippets (with context) for the LLM ----
            ranked = triage.rank(iter(scoped), sensitive_files, budget, extra_globs)
            by_file: Dict[str, Dict[int, str]] = {}
            for p, ln, c in scoped:
                by_file.setdefault(p, {})[ln] = c
            snippets = [self._snippet(entry, by_file) for entry in ranked]

            if plan and shard is not None:
                coverage = {"scope_lines": len(scoped), "snippet_lines": len(snippets), "shard": shard}
                sly_data.setdefault("review_coverage", {})[str(shard)] = coverage
                logger.info("run %s: secret_scanner shard %s — %d finding(s), %d/%d snippet(s)",
                            run_id, shard, len(findings), len(snippets), len(scoped))
                return {"findings": findings, "review_snippets": snippets, "coverage": coverage}

            logger.info("run %s: secret_scanner %d finding(s), %d added line(s)",
                        run_id, len(findings), len(scoped))
            # legacy shape: added_lines is the ranked (capped) snippet code
            return {"findings": findings,
                    "added_lines": [{"file": s["file"], "line": s["line"], "code": s["code"]} for s in snippets],
                    "review_snippets": snippets}
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _shard_files(plan: Dict[str, Any], shard: int) -> Optional[set]:
        for s in plan.get("shards", []):
            if s.get("shard") == shard:
                return set(s.get("files", []))
        return None

    @staticmethod
    def _snippet(entry: Dict[str, Any], by_file: Dict[str, Dict[int, str]]) -> Dict[str, Any]:
        path, line = entry["file"], entry["line"]
        near = by_file.get(path, {})
        context = [{"line": ln, "code": near[ln]}
                   for ln in range(line - 3, line + 4) if ln in near]
        return {"file": path, "line": line, "code": entry["code"],
                "why_flagged": entry["why"], "context": context}

    @staticmethod
    def _finding(fid, category, severity, path, line, cwe, title, explanation, fix) -> Dict[str, Any]:
        return {"id": fid, "category": category, "severity": severity, "file": path,
                "line_start": line, "line_end": line, "cwe": cwe, "title": title,
                "explanation": explanation, "fix_suggestion": fix, "source": "tool"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
