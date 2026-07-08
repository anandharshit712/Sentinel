"""secret_scanner_tool (04 §5.4, A2) — scans newly-added diff lines for hardcoded secrets.

Reads change_profile from sly_data, walks the '+' (added) lines of every hunk and matches them
against a secret ruleset + a high-entropy heuristic. Every hit is a Critical finding (04 §5.3
instruction 5: "Hardcoded credentials/secrets are ALWAYS critical"). Returns the findings to the
security_review_agent, which merges them and persists via contract_store. Only added lines are
scanned so pre-existing secrets in untouched code are not re-flagged.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any, Dict, Iterator, List, Tuple, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.secret_scanner")

# (category, regex, cwe, title) — specific rules first; one finding per line, first match wins.
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


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((n / len(s)) * math.log2(n / len(s))
                for n in (s.count(c) for c in set(s)))


def _added_lines(profile: Dict[str, Any]) -> Iterator[Tuple[str, int, str]]:
    """(path, new_line_number, content) for every added ('+') line in the diff."""
    for f in profile.get("files", []):
        for h in f.get("hunks", []):
            offset = 0
            for line in h.get("patch", "").splitlines()[1:]:  # skip the @@ header
                if line.startswith("+") and not line.startswith("+++"):
                    yield f["path"], h.get("new_start", 0) + offset, line[1:]
                    offset += 1
                elif not line.startswith("-"):
                    offset += 1  # context line advances the new-file counter (none under -U0)


class SecretScannerTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            findings: List[Dict[str, Any]] = []
            n = 0
            for path, lineno, content in _added_lines(profile):
                cat = cwe = title = None
                for c, rx, w, t in _RULES:
                    if rx.search(content):
                        cat, cwe, title = c, w, t
                        break
                if cat is None:
                    m = _QUOTED.search(content)
                    if m and _entropy(m.group(1)) >= 4.0:
                        cat, cwe, title = "high_entropy_secret", "CWE-798", "High-entropy secret string"
                if cat is None:
                    continue
                n += 1
                findings.append({
                    "id": f"SEC-{n:03d}",
                    "category": cat,
                    "severity": "critical",  # secrets are always critical (04 §5.3)
                    "file": path,
                    "line_start": lineno,
                    "line_end": lineno,
                    "cwe": cwe,
                    "title": title,
                    "explanation": f"{title} detected in an added line of {path}.",
                    "fix_suggestion": "Remove the secret from source and rotate it; load from a secrets manager or env var.",
                    "source": "tool",
                })
            logger.info("run %s: secret_scanner %d finding(s)", run_id, len(findings))
            return {"findings": findings}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
