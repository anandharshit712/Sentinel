"""decision_logger_tool (04 §5.14, A7) — validates and persists the promotion Decision.

Final bookkeeping step of promotion_gating_agent: validates the LLM-assembled Decision, writes it
to sly_data as the tool-owned `decision` contract, and transactionally inserts the decisions row
(+ a pending approvals row when approval is required) plus an audit event.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from db import dao

logger = logging.getLogger("coded_tools.decision_logger")


class DecisionLoggerTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            decision = args.get("decision", args)
            if not isinstance(decision, dict) or "decision" not in decision:
                return "Error: missing decision payload"
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
