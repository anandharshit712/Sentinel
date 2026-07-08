"""incident_history_tool (04 §5.10, A5) — recent incident stats for the target repo+env.

Feeds the environment_context_agent (which assembles env_context). Reads repo + target env from
the sly_data event and returns 7-day and 30-day incident counts via the shared DAO.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from db import dao

logger = logging.getLogger("coded_tools.incident_history")


class IncidentHistoryTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            event = sly_data.get("event") or {}
            repo = args.get("repo") or (event.get("repo") or {}).get("name")
            env = args.get("env") or (event.get("target_transition") or {}).get("to_env")
            if not (repo and env):
                return "Error: missing repo/target env (seed sly_data.event or pass args)"
            wk = dao.recent_incidents(repo, env, days=7)
            mo = dao.recent_incidents(repo, env, days=30)
            out = {"target_env": env, "count_7d": wk["count"], "count_30d": mo["count"],
                   "most_recent_at": mo["most_recent_at"]}
            logger.info("run %s: incident_history %s/%s 7d=%d 30d=%d", run_id, repo, env,
                        out["count_7d"], out["count_30d"])
            return out
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
