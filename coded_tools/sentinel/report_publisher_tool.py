"""report_publisher_tool (04 §5, A7) — synthesizes + persists the ReviewReport.

Two modes:
- LLM-authored: called with a complete ReviewReport (review_synthesis_agent path, 04 §5).
- Deterministic synthesis (default in B2): called with no report, it reads security_findings +
  quality_findings from sly_data and merges/scores them IN CODE. Chosen because sly_data is
  invisible to LLMs (user_guide "Sly data") so findings cannot flow agent->agent as "inputs", and
  because dedup + health-score arithmetic are exactly the deterministic decisions rule 4 ("LLM
  reasons, code decides") keeps out of the model. [design note: backport to 01 like the §9 items.]

Either way it validates, writes sly_data["review_report"], persists to Postgres, and (SIMULATE_CICD)
no-ops the PR/MR comment.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from db import dao

logger = logging.getLogger("coded_tools.report_publisher")

_SEV_DEDUCT = {"critical": 25, "high": 10, "medium": 4, "low": 1}
_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _dedup(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge findings sharing (file, category, line_start); keep the max severity."""
    by_key: Dict[tuple, Dict[str, Any]] = {}
    for f in findings:
        key = (f.get("file"), f.get("category"), f.get("line_start"))
        cur = by_key.get(key)
        if cur is None or _SEV_RANK[f["severity"]] < _SEV_RANK[cur["severity"]]:
            by_key[key] = f
    return sorted(by_key.values(), key=lambda f: (_SEV_RANK[f["severity"]], f.get("source") != "tool"))


def _synthesize(sly_data: Dict[str, Any]) -> Dict[str, Any]:
    sec = (sly_data.get("security_findings") or {}).get("findings", []) or []
    qual = (sly_data.get("quality_findings") or {}).get("findings", []) or []
    merged = _dedup(sec + qual)
    counts = {s: sum(1 for f in merged if f["severity"] == s) for s in _SEV_DEDUCT}
    score = max(0, 100 - sum(_SEV_DEDUCT[f["severity"]] for f in merged))
    if counts["critical"]:
        rec = "request_changes"
    elif counts["high"]:
        rec = "approve_with_changes"
    else:
        rec = "approve"
    worst = merged[0]["title"] if merged else "no issues found"
    summary = (f"{len(merged)} finding(s): {counts['critical']} critical, {counts['high']} high, "
               f"{counts['medium']} medium, {counts['low']} low. Worst: {worst}.")
    return {"executive_summary": summary, "findings": merged, "pr_health_score": score,
            "recommendation": rec, "counts": counts}


class ReportPublisherTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            report = args.get("review_report")
            if not (isinstance(report, dict) and "pr_health_score" in report):
                report = _synthesize(sly_data)  # deterministic B2 path
            wrapped = contracts.wrap(report, run_id=str(run_id), produced_by="review_synthesis")
            contracts.validate("review_report", wrapped)
            sly_data["review_report"] = wrapped
            dao.save_run_payload("review_reports", str(run_id), wrapped,
                                 pr_health_score=report["pr_health_score"],
                                 recommendation=report["recommendation"])
            simulate = os.environ.get("SIMULATE_CICD", "true").lower() == "true"
            publish = {"action": "none", "detail": "simulated"} if simulate \
                else {"action": "pr_comment", "detail": "requested via gateway"}
            logger.info("run %s: report_publisher persisted review_report (%s)", run_id, publish["action"])
            return {"published": True, "persisted": "review_reports", "publish": publish,
                    "recommendation": report["recommendation"], "health_score": report["pr_health_score"],
                    "counts": report.get("counts", {})}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
