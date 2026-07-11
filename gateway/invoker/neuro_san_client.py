"""Streaming Neuro-SAN client (04 §7.1) — the only module that touches the real server.

Drives the `sentinel` network over HTTP exactly like scripts/verify_b*.py: seed sly_data
(event, run_id, repo_workspace), stream, surface AGENT_FRAMEWORK(101) progress to a callback,
and return the terminal AI(4) structure + allow-listed sly_data (per the frontman's
allow.to_upstream list: run_id, change_profile, review_plan, review_report, test_plan,
test_results, env_context, risk_score, decision).

This is a BLOCKING call (the studio client is a sync generator). The Gateway runs it in a
thread (asyncio.to_thread) and marshals on_progress back to the event loop.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from neuro_san.client.agent_session_factory import AgentSessionFactory
from neuro_san.client.streaming_input_processor import StreamingInputProcessor

Progress = Callable[[dict], None]


def invoke_network(
    run_id: str,
    event: dict,
    workspace: str,
    *,
    host: str,
    port: int,
    network: str = "sentinel",
    on_progress: Progress | None = None,
) -> tuple[dict | None, dict, str]:
    """Run one DeliveryEvent through the network. Returns (structure, sly_data, answer).

    Raises on transport/stream failure so the caller marks the run `failed`.
    """
    session = AgentSessionFactory().create_session("http", network, hostname=host, port=port)
    proc = StreamingInputProcessor(session=session)
    mp = proc.get_message_processor()
    sly = {"run_id": run_id, "event": event, "repo_workspace": workspace}
    # MAXIMAL filter so per-agent/tool progress ("Invoking: `x`") streams for the state machine + SSE
    req = proc.formulate_chat_request(
        "Process this DeliveryEvent: " + json.dumps(event), sly,
        chat_filter={"chat_filter_type": "MAXIMAL"})

    for r in session.streaming_chat(req):
        resp: dict[str, Any] = r.get("response", {}) or {}
        mp.process_message(resp, r.get("type"))
        if on_progress:
            text = resp.get("text") or ""
            if text.startswith("Invoking: `"):  # the one clean per-agent/tool stage marker
                invoked = text.split("`", 2)[1] if "`" in text else ""
                origin = [o.get("tool", "") for o in (resp.get("origin") or [])]
                on_progress({"text": f"Invoking {invoked}", "invoked": invoked, "origin": origin})

    return mp.get_structure(), (mp.get_sly_data() or {}), (mp.get_answer() or "")
