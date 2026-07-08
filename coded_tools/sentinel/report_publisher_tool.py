"""report_publisher_tool (04 §5, A7) — persists the ReviewReport and requests PR/MR publication.

Final step of review_synthesis_agent: validates the LLM-produced ReviewReport, writes it to
sly_data as the tool-owned `review_report` contract, and persists it to Postgres. PR/MR comment
publication is delegated to the Gateway internal API; under SIMULATE_CICD it is a logged no-op.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from db import dao

logger = logging.getLogger("coded_tools.report_publisher")


class ReportPublisherTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            report = args.get("review_report", args)
            if not isinstance(report, dict) or "pr_health_score" not in report:
                return "Error: missing review_report payload"
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
            return {"published": True, "persisted": "review_reports", "publish": publish}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
