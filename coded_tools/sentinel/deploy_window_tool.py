"""deploy_window_tool (04 §5.11, A5) — is now a risky time to deploy?

Reads the repo's risky_windows + freeze_dates + timezone from repo_config.yaml and evaluates them
against the current time in that timezone. Returns {risky, reason} for the environment_context_agent.
`now` may be injected via args for deterministic tests.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, Union
from zoneinfo import ZoneInfo

import yaml

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.deploy_window")

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _match_window(now: dt.datetime, window: str) -> bool:
    """window is 'Day' (whole day) or 'Day HH:MM-HH:MM'."""
    parts = window.split()
    if _DAYS.get(parts[0][:3].lower()) != now.weekday():
        return False
    if len(parts) == 1:
        return True
    start, end = parts[1].split("-")
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    cur = now.hour * 60 + now.minute
    return sh * 60 + sm <= cur <= eh * 60 + em


class DeployWindowTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            event = sly_data.get("event") or {}
            repo = args.get("repo") or (event.get("repo") or {}).get("name")
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            rc = (cfg.get("repos") or {}).get(repo) or {}
            tz = ZoneInfo(rc.get("timezone", "UTC"))
            now = (dt.datetime.fromisoformat(args["now"]) if args.get("now")
                   else dt.datetime.now(tz)).astimezone(tz)

            today = now.date().isoformat()
            if today in (rc.get("freeze_dates") or []):
                return self._verdict(run_id, True, f"deploy freeze on {today}")
            for w in (rc.get("risky_windows") or []):
                if _match_window(now, w):
                    return self._verdict(run_id, True, f"risky window '{w}'")
            return self._verdict(run_id, False, "outside risky windows")
        except Exception as e:
            return f"Error: {e}"

    def _verdict(self, run_id, risky, reason):
        logger.info("run %s: deploy_window risky=%s (%s)", run_id, risky, reason)
        return {"risky": risky, "reason": reason}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
