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


class ReviewDigestTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            merged: List[Dict[str, Any]] = []
            counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for key in _KEYS:
                for f in ((sly_data.get(key) or {}).get("findings") or []):
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
