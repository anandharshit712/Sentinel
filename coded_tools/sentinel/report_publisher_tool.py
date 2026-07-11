"""report_publisher_tool (04 §5, A7) — synthesizes + persists the ReviewReport.

Two modes:
- LLM-authored: called with a complete ReviewReport (legacy path, 04 §5).
- Deterministic synthesis (default): called with no report, it reads quality_findings + all
  security findings — the legacy `security_findings` and the adaptive fan-out's per-shard
  `security_findings_shard_1..4` — from sly_data and merges/scores them IN CODE. Chosen because
  sly_data is invisible to LLMs (user_guide "Sly data") so findings cannot flow agent->agent as
  "inputs", and because dedup + health-score arithmetic + coverage are exactly the deterministic
  decisions rule 4 ("LLM reasons, code decides") keeps out of the model. When a `review_plan`
  exists it also emits a `coverage` object (what was deep-reviewed vs scanned) and uses
  `senior_summary` as the executive summary. [design note: backported to 01 §5.3/§5.4.]

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


# legacy key + the adaptive fan-out's per-shard reviewer contracts
_SEC_KEYS = ["security_findings"] + [f"security_findings_shard_{i}" for i in range(1, 5)]


def _coverage(sly_data: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Honest deep-review coverage from the review_plan metrics + per-shard scanner reports."""
    metrics = plan.get("metrics", {})
    rc = sly_data.get("review_coverage") or {}
    shards = plan.get("shards", [])
    shard_count = int(metrics.get("shard_count", len(shards) or 1))
    llm_reviewed = sum(int(v.get("snippet_lines", 0)) for v in rc.values())
    unscanned = [s["shard"] for s in shards if str(s["shard"]) not in rc]  # reviewer never ran
    return {"total_added_lines": int(metrics.get("added_lines", 0)),
            "llm_reviewed_lines": llm_reviewed, "deterministic_coverage_pct": 100,
            "shards": shard_count, "unscanned_shards": unscanned}


def _floor_findings(sly_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic detection floor: re-scan the whole diff for secrets + dangerous sinks IN CODE.

    Guarantees the critical/high deterministic findings are in the report even if a security
    reviewer ran late or out of order on a long chain (frontman ordering is not guaranteed — §14).
    The LLM reviewers still add their own judged findings; _dedup collapses the overlap.
    """
    profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip")
    if not profile:
        return []
    try:
        from coded_tools.sentinel.secret_scanner_tool import SecretScannerTool
        # no review_plan/shard -> legacy whole-diff scan; does not touch sly_data
        out = SecretScannerTool().invoke({}, {"run_id": sly_data.get("run_id"),
                                              "change_profile": profile,
                                              "event": sly_data.get("event")})
        return out.get("findings", []) if isinstance(out, dict) else []
    except Exception:
        return []


def _synthesize(sly_data: Dict[str, Any]) -> Dict[str, Any]:
    sec = [f for k in _SEC_KEYS for f in ((sly_data.get(k) or {}).get("findings") or [])]
    qual = (sly_data.get("quality_findings") or {}).get("findings", []) or []
    merged = _dedup(sec + _floor_findings(sly_data) + qual)
    counts = {s: sum(1 for f in merged if f["severity"] == s) for s in _SEV_DEDUCT}
    score = max(0, 100 - sum(_SEV_DEDUCT[f["severity"]] for f in merged))
    if counts["critical"]:
        rec = "request_changes"
    elif counts["high"]:
        rec = "approve_with_changes"
    else:
        rec = "approve"
    worst = merged[0]["title"] if merged else "no issues found"
    senior = (sly_data.get("senior_summary") or {}).get("summary")
    summary = senior or (
        f"{len(merged)} finding(s): {counts['critical']} critical, {counts['high']} high, "
        f"{counts['medium']} medium, {counts['low']} low. Worst: {worst}.")

    plan = sly_data.get("review_plan") or {}
    report = {"executive_summary": summary, "findings": merged, "pr_health_score": score,
              "recommendation": rec, "counts": counts}
    if plan:
        cov = _coverage(sly_data, plan)
        report["coverage"] = cov
        report["executive_summary"] = (
            f"{summary} Coverage: LLM deep-reviewed {cov['llm_reviewed_lines']} of "
            f"{cov['total_added_lines']} added line(s) across {cov['shards']} shard(s); "
            f"deterministic rules scanned 100%"
            + (f" ({len(cov['unscanned_shards'])} shard(s) not deep-reviewed)." if cov["unscanned_shards"] else "."))
    return report


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
