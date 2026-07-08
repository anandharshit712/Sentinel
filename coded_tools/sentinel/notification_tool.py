"""notification_tool (04 §5.16, A7) — dashboard + optional Slack/Teams notification.

Always inserts a dashboard notification row; if NOTIFY_WEBHOOK_URL is set it also POSTs a deep-link
to Slack/Teams. Both are best-effort: any delivery failure is logged and the run continues — a
notification never fails the pipeline (only an invalid `kind` is a caller error).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from db import dao

logger = logging.getLogger("coded_tools.notification")


class NotificationTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        kind = args.get("kind")
        if kind not in ("hold", "escalate"):
            return "Error: kind must be 'hold' or 'escalate'"
        summary = args.get("summary", "")
        delivered = {"dashboard": False, "webhook": False}
        try:
            dao.insert_notification(None if run_id == "?" else str(run_id), kind, summary)
            delivered["dashboard"] = True
        except Exception as e:  # non-fatal (04 §5.16)
            logger.warning("run %s: notification DB insert failed (non-fatal): %s", run_id, e)

        url = os.environ.get("NOTIFY_WEBHOOK_URL")
        if url:
            try:
                import json
                import urllib.request
                link = f"…/runs/{run_id}"
                body = json.dumps({"text": f"[{kind}] {summary} {link}"}).encode()
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3).close()
                delivered["webhook"] = True
            except Exception as e:  # non-fatal
                logger.warning("run %s: notification webhook failed (non-fatal): %s", run_id, e)

        logger.info("run %s: notification kind=%s delivered=%s", run_id, kind, delivered)
        return {"notified": True, "kind": kind, "delivered": delivered}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
