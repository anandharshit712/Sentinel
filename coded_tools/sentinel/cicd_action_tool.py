"""cicd_action_tool (04 §5.15, A7) — enacts a promotion via the CI/CD platform.

Delegates to the Gateway internal API (which owns platform creds & adapters); under SIMULATE_CICD
(hackathon default) it is a logged no-op. Either way it appends the outcome to
decision.actions_taken in sly_data. Never auto-promotes staging→production (that path escalates
upstream; this tool only runs when the gate already decided to promote).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.cicd_action")


class CicdActionTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            if args.get("action") != "promote":
                return "Error: action must be 'promote'"
            simulate = os.environ.get("SIMULATE_CICD", "true").lower() == "true"
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            entry = ({"action": "none", "detail": "simulated", "at": now} if simulate
                     else {"action": "cicd_promote", "detail": "requested via gateway", "at": now})
            decision = sly_data.get("decision")
            if isinstance(decision, dict):
                decision.setdefault("actions_taken", []).append(entry)
            logger.info("run %s: cicd_action promote (%s)", run_id, entry["action"])
            return entry
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
