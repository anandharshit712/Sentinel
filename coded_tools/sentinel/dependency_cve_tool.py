"""dependency_cve_tool (04 §5.5, A2) — flags added dependencies with known advisories.

Reads change_profile from sly_data, extracts dependencies added in manifest diffs
(requirements.txt, package.json), and looks each up in an offline OSV snapshot
(hackathon scope is snapshot-only; live OSV is Phase 7 — see cut line §13). Matching advisories
become findings returned to the security_review_agent. Network egress never happens here, so this
tool cannot fail on OSV being unreachable (risk register: snapshot fallback).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Tuple, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.dependency_cve")

_REQ = re.compile(r"^([A-Za-z0-9._-]+)\s*(?:[=<>!~]=?\s*([0-9][0-9A-Za-z.\-]*))?")
_PKGJSON = re.compile(r'"([A-Za-z0-9._@/-]+)"\s*:\s*"[\^~>=<]*([0-9][0-9A-Za-z.\-]*)"')


def _ver_tuple(v: str) -> Tuple[int, ...]:
    parts = []
    for p in v.split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def _affected(version: str, specifiers: List[str]) -> bool:
    """True if version matches any specifier. No version known => report (conservative)."""
    if version is None:
        return True
    for spec in specifiers:
        spec = spec.strip()
        if spec == "*":
            return True
        m = re.match(r"(<=|>=|==|<|>)?\s*(.+)", spec)
        op, target = (m.group(1) or "=="), m.group(2)
        a, b = _ver_tuple(version), _ver_tuple(target)
        if (op == "<" and a < b) or (op == "<=" and a <= b) or (op == "==" and a == b) \
           or (op == ">" and a > b) or (op == ">=" and a >= b):
            return True
    return False


def _added_deps(profile: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """(package, version|None, manifest_path) for deps added in requirements.txt / package.json."""
    deps: List[Tuple[str, str, str]] = []
    for f in profile.get("files", []):
        path = f.get("path", "")
        is_req = path.endswith("requirements.txt")
        is_pkg = path.endswith("package.json")
        if not (is_req or is_pkg):
            continue
        for h in f.get("hunks", []):
            for line in h.get("patch", "").splitlines()[1:]:
                if not line.startswith("+") or line.startswith("+++"):
                    continue
                content = line[1:].strip()
                if is_req:
                    m = _REQ.match(content)
                    if m and content and not content.startswith("#"):
                        deps.append((m.group(1).lower(), m.group(2), path))
                else:
                    m = _PKGJSON.search(content)
                    if m:
                        deps.append((m.group(1).lower(), m.group(2), path))
    return deps


class DependencyCveTool(CodedTool):
    def __init__(self, osv_snapshot_path: str = "config/osv_snapshot.json"):
        self.osv_snapshot_path = osv_snapshot_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            with open(self.osv_snapshot_path, encoding="utf-8") as fh:
                snapshot = json.load(fh)
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            findings: List[Dict[str, Any]] = []
            n = 0
            for pkg, version, path in _added_deps(profile):
                for adv in snapshot.get(pkg, []):
                    if not _affected(version, adv.get("affected", ["*"])):
                        continue
                    n += 1
                    ver_txt = version or "unpinned"
                    findings.append({
                        "id": f"SEC-CVE-{n:03d}",
                        "category": "dependency_cve",
                        "severity": adv.get("severity", "medium"),
                        "file": path,
                        "cwe": adv.get("cwe", ""),
                        "title": f"{pkg} {ver_txt}: {adv['id']}",
                        "explanation": f"{adv.get('summary', adv['id'])} (from offline OSV snapshot).",
                        "fix_suggestion": f"Upgrade {pkg} to {adv.get('fixed', 'a fixed version')}.",
                        "source": "tool",
                    })
            logger.info("run %s: dependency_cve %d finding(s)", run_id, len(findings))
            return {"findings": findings}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
