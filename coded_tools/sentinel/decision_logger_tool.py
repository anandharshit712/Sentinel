"""decision_logger_tool (04 §5.14, A7) — builds/validates and persists the promotion Decision.

Final bookkeeping step of promotion_gating_agent. Two modes:
- LLM-passed: validate the Decision handed in.
- Deterministic (default in B4): build the Decision in code from sly_data — the trust_ladder
  verdict (ladder_verdict), the event transition, and the risk/review/test/env contracts for the
  reasoning trail. Same rationale as report_publisher: the consumer LLM can't read sly_data, and
  assembling a versioned contract is a deterministic job (rule 4 "code decides").
Then writes sly_data["decision"] and transactionally inserts decisions (+ pending approval when
required) + an audit event.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from db import dao

logger = logging.getLogger("coded_tools.decision_logger")


def _build_decision(sly_data: Dict[str, Any]):
    verdict = sly_data.get("ladder_verdict") or {}
    if "decision" not in verdict:
        return None
    event = sly_data.get("event") or {}
    risk = sly_data.get("risk_score") or {}
    rr = sly_data.get("review_report") or {}
    tr = sly_data.get("test_results") or {}
    tp = sly_data.get("test_plan") or {}
    ec = sly_data.get("env_context") or {}
    dec = verdict["decision"]
    totals = tr.get("totals") or {}
    return {
        "decision": dec,
        "transition": event.get("target_transition") or {},
        "policy_version": verdict.get("policy_version", "unknown"),
        "rule_fired": verdict.get("rule_fired", "unknown"),
        "reasoning_trail": {
            "review": rr.get("executive_summary") or f"counts={rr.get('counts', {})}",
            "testing": f"{len(tp.get('selected', []))} tests selected ({tp.get('selection_confidence', 'n/a')})",
            "results": (f"passed={totals.get('passed', 0)} failed={totals.get('failed', 0)}"
                        + (" stage_failure" if tr.get("stage_failure") or tr.get("timed_out") else "")),
            "context": ec.get("summary") or f"incidents_7d={(ec.get('incidents') or {}).get('count_7d', 0)}, "
                       f"risky_window={(ec.get('deploy_window') or {}).get('risky')}",
            "policy": f"{verdict.get('rule_fired', '?')} @ risk {risk.get('score', '?')}/{risk.get('band', '?')}",
        },
        "approval_required": dec == "escalate",
        "approval_status": "pending" if dec == "escalate" else "n/a",
    }


class DecisionLoggerTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            decision = args.get("decision")
            if not (isinstance(decision, dict) and "decision" in decision and "transition" in decision):
                decision = _build_decision(sly_data)  # deterministic B4 path
            if not decision:
                return "Error: no decision payload and no ladder_verdict in sly_data"
            wrapped = contracts.wrap(decision, run_id=str(run_id), produced_by="promotion_gating")
            contracts.validate("decision", wrapped)
            sly_data["decision"] = wrapped
            dao.insert_decision(str(run_id), decision)
            logger.info("run %s: decision=%s approval_required=%s", run_id,
                        decision["decision"], decision["approval_required"])
            return {"logged": True, "decision": decision["decision"],
                    "approval_required": decision["approval_required"]}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
