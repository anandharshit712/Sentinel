"""review_digest_tool (04 §5.19, A8) — compact roll-up of all security findings for the senior agent.

LLMs can't read sly_data, so `senior_security_agent` calls this to get a small
`{id, severity, file, title}` summary of every shard reviewer's findings without re-reading raw
code. Deterministic, read-only; caps output so the senior narrative stays cheap.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.review_digest")

_CAP = 80
_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_KEYS = ["security_findings"] + [f"security_findings_shard_{i}" for i in range(1, 5)]


def _security_floor(sly_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Secret/sink + dependency-advisory findings over the whole diff, recomputed in code."""
    profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip")
    if not profile:
        return []
    from coded_tools.sentinel.dependency_cve_tool import DependencyCveTool
    from coded_tools.sentinel.secret_scanner_tool import SecretScannerTool

    scan_input = {"run_id": sly_data.get("run_id"), "change_profile": profile,
                  "event": sly_data.get("event"), "repo_workspace": sly_data.get("repo_workspace")}
    out: List[Dict[str, Any]] = []
    for tool in (SecretScannerTool(), DependencyCveTool()):
        try:
            res = tool.invoke({}, dict(scan_input))
            if isinstance(res, dict):
                out.extend(res.get("findings", []))
        except Exception:   # a floor that cannot run must not blank the reviewers' findings
            continue
    return out


class ReviewDigestTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            merged: List[Dict[str, Any]] = []
            counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            # Reviewers store only their OWN findings (copying 50 tool findings back out as tokens
            # timed the call out on large audits), so the deterministic security floor is added
            # here in code, exactly as report_publisher does for the report.
            seen = set()
            for f in _security_floor(sly_data) + [f for key in _KEYS
                                                  for f in ((sly_data.get(key) or {}).get("findings") or [])]:
                # a reviewer that still copies a tool finding lands on the same line+category
                k = ((f.get("file"), f.get("line_start"), f.get("category"))
                     if f.get("line_start") is not None else f.get("id"))
                if k in seen:
                    continue
                seen.add(k)
                sev = f.get("severity", "low")
                counts[sev] = counts.get(sev, 0) + 1
                merged.append({"id": f.get("id"), "severity": sev,
                               "file": f.get("file"), "title": f.get("title")})
            merged.sort(key=lambda d: (_SEV_RANK.get(d["severity"], 9), str(d["file"]), str(d["id"])))
            logger.info("run %s: review_digest %d finding(s) %s", run_id, len(merged), counts)
            return {"findings": merged[:_CAP], "counts": counts, "total": len(merged)}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
