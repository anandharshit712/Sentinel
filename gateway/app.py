"""Sentinel Delivery Gateway (04 §7) — FastAPI intake, run state machine, SSE, approvals.

Demo/hackathon scope: `POST /api/v1/simulate` drives the real Neuro-SAN network (the invoker),
maps AGENT_FRAMEWORK progress to the run state machine, and streams it over SSE. GitHub webhook
adapter (C3) and internal cicd endpoints are Phase-7 / off the demo path.

Run state machine (04 §7.2): received -> analyzing -> reviewing -> testing -> scoring -> gated
-> done | failed. Transitions are driven by streamed agent/tool progress; any state may -> failed.

# ponytail: single-process, in-memory SSE bus + blocking DB writes on the loop. Fine for a
# single-user demo; for multi-worker prod use Redis pub/sub + a durable events table (Phase 7).
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import hmac
import ipaddress
import json
import os
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db import dao
from gateway import settings
from gateway.invoker.neuro_san_client import invoke_network
from lib import workspace
from lib.redact import redact

app = FastAPI(title="Sentinel Delivery Gateway", version="1")

# ---------------------------------------------------------------- state machine
_STATE_ORDER = ["received", "analyzing", "reviewing", "testing", "scoring", "gated", "done", "failed"]


def _rank(state: str) -> int:
    return _STATE_ORDER.index(state) if state in _STATE_ORDER else -1


# progress keyword -> canonical state (first match in this order wins)
_STAGE_KEYWORDS = [
    ("analyzing", ("change_analysis", "git_diff", "ast_analyzer", "dependency_graph")),
    ("reviewing", ("security", "quality", "secret", "complexity", "cve", "report_publisher", "review")),
    ("testing", ("test_selection", "test_mapper", "test_runner", "test")),
    ("scoring", ("environment", "incident", "deploy_window", "risk", "trust_ladder", "score")),
    ("gated", ("promotion_gating", "decision", "cicd", "notification", "gat")),
]


def _derive_state(progress: dict) -> str | None:
    hay = (progress.get("text", "") + " " + " ".join(progress.get("origin", []))).lower()
    for state, kws in _STAGE_KEYWORDS:
        if any(k in hay for k in kws):
            return state
    return None


# ---------------------------------------------------------------- in-memory SSE bus
class _Bus:
    def __init__(self) -> None:
        self.logs: dict[str, list[dict]] = {}
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.seq: dict[str, int] = {}

    def publish(self, run_id: str, ev: dict) -> None:
        self.seq[run_id] = self.seq.get(run_id, 0) + 1
        ev = {"seq": self.seq[run_id], "run_id": run_id,
              "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), **ev}
        self.logs.setdefault(run_id, []).append(ev)
        for q in list(self.subs.get(run_id, ())):
            q.put_nowait(ev)

    def replay(self, run_id: str) -> list[dict]:
        return list(self.logs.get(run_id, ()))

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        self.subs.get(run_id, set()).discard(q)


bus = _Bus()


def _terminal(ev: dict) -> bool:
    return ev.get("kind") == "state_change" and ev.get("state") in ("done", "failed")


# ---------------------------------------------------------------- auth shim
def _role_for(token: str | None) -> str | None:
    """Resolve a bare token to its role. OPEN_MODE -> admin; unknown token -> None."""
    if settings.OPEN_MODE:
        return "admin"
    tok = (token or "").strip()
    for known, role in settings.API_TOKENS.items():
        if hmac.compare_digest(known, tok):  # constant-time; avoids a token timing oracle (L3)
            return role
    return None


def _check(role: str | None, min_role: str) -> str:
    if not role:
        raise HTTPException(401, "invalid or missing token")
    if settings.ROLE_RANK[role] < settings.ROLE_RANK[min_role]:
        raise HTTPException(403, f"requires {min_role}")
    return role


def _require(min_role: str):
    def dep(authorization: str | None = Header(default=None)) -> str:
        tok = (authorization or "").removeprefix("Bearer ").strip()
        return _check(_role_for(tok), min_role)
    return dep


# ---------------------------------------------------------------- pipeline runner
_ALLOWED_CLONE_SCHEMES = {"https"}


def _validate_clone_url(url: str) -> str:
    """Reject anything that isn't a plain https:// URL to a public host (C2).

    Closes ext:: transport RCE, file:// local reads, `-`-prefixed option injection, and SSRF to
    internal/loopback/link-local addresses. The GitHub Action's
    https://x-access-token:<token>@github.com/... still passes (https + public host).
    """
    if not url or url.startswith("-"):
        raise ValueError("event.repo.url is empty or option-like")
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_CLONE_SCHEMES:
        raise ValueError(f"repo.url scheme must be https (got {parts.scheme!r})")
    host = parts.hostname
    if not host:
        raise ValueError("repo.url has no host")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except socket.gaierror as e:
        raise ValueError(f"repo.url host does not resolve: {host}") from e
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("repo.url resolves to a non-public address (SSRF blocked)")
    return url


def _redact_url(url: str) -> str:
    """Drop userinfo (user:pass@) from a URL so tokens aren't persisted/served (H2)."""
    try:
        p = urlsplit(url)
        if p.username or p.password:
            netloc = p.hostname or ""
            if p.port:
                netloc += f":{p.port}"
            return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    except Exception:
        pass
    return url


def _clone(event: dict, run_id: str) -> str:
    """Shallow-clone the repo for this run; returns the workspace path."""
    url = _validate_clone_url(((event.get("repo") or {}).get("url") or "").strip())
    ws = str(workspace.ensure_workspace(run_id))
    # full clone (local sample repos are tiny; both base/head shas must be reachable for git diff)
    # ponytail: full clone; switch to --filter=blob:none partial clone if a big repo shows up
    # -c core.longpaths=true: survive Windows MAX_PATH (260) on deeply-nested repos (exit 128)
    # C2 defense-in-depth: GIT_ALLOW_PROTOCOL/protocol.ext.allow disable ext::/file://; '--' stops
    # an option-like url being read as a flag even if _validate_clone_url is somehow bypassed.
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", "-c", "protocol.ext.allow=never", "-c", "core.longpaths=true",
                        "clone", "--quiet", "--", url, ws],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:  # surface git's real stderr, not just the exit code (e.g. "Filename too long")
        raise RuntimeError(f"git clone failed (exit {r.returncode}): {(r.stderr or r.stdout or '').strip()[:400]}")
    return ws


def _advance(run_id: str, state: str) -> None:
    """Move the run forward to `state` if it is ahead of the current one (monotonic)."""
    cur = (dao.get_run(run_id) or {}).get("state", "received")
    if _rank(state) > _rank(cur):
        dao.set_run_state(run_id, state)
        bus.publish(run_id, {"kind": "state_change", "state": state})


def _persist_contracts(run_id: str, sly: dict) -> None:
    """Durably store the run's contract payloads from the returned (allow-listed) sly_data.

    review_report + decision are already persisted by their coded tools; risk_score/test_results/
    test_plan/env_context live only in sly_data, so the Gateway lands them for the dashboard.
    Best-effort: a missing/partial contract is skipped, never fails the run.
    """
    rs = sly.get("risk_score") or {}
    if {"score", "band", "formula_version"} <= rs.keys():
        dao.save_run_payload("risk_scores", run_id, rs, score=int(rs["score"]),
                             band=rs["band"], formula_version=rs["formula_version"])
    tr = sly.get("test_results") or {}
    if tr:
        t = tr.get("totals") or {}
        dao.save_run_payload("test_results", run_id, tr, passed=t.get("passed"),
                             failed=t.get("failed"), skipped=t.get("skipped"),
                             timed_out=bool(tr.get("timed_out", False)),
                             duration_seconds=tr.get("duration_seconds"))
    tp = sly.get("test_plan") or {}
    if tp:
        dao.save_run_payload("test_plans", run_id, tp,
                             selection_confidence=tp.get("selection_confidence"))
    ec = sly.get("env_context") or {}
    if ec:
        dao.save_run_payload("env_contexts", run_id, ec)


async def _run_pipeline(run_id: str, event: dict, ws_override: str | None) -> None:
    loop = asyncio.get_running_loop()

    def on_progress(p: dict) -> None:  # called from the invoker worker thread
        loop.call_soon_threadsafe(_on_progress, run_id, p)

    try:
        ws = ws_override or await asyncio.to_thread(_clone, event, run_id)
        _advance(run_id, "analyzing")
        structure, sly, _answer = await asyncio.to_thread(
            invoke_network, run_id, event, ws,
            host=settings.NEURO_SAN_HOST, port=settings.NEURO_SAN_PORT,
            network=settings.NEURO_SAN_NETWORK, on_progress=on_progress)
        _persist_contracts(run_id, sly)
        decision = (sly.get("decision") or {}).get("decision") \
            or (dao.get_decision(run_id) or {}).get("decision")
        dao.set_run_state(run_id, "done", finished=True)
        bus.publish(run_id, {"kind": "state_change", "state": "done",
                             "decision": decision, "structure": structure})
    except Exception as e:  # transport/stream/clone failure -> failed, re-runnable
        msg = redact(str(e))[:500]  # M3: scrub credentials (e.g. tokenized clone url in git stderr)
        dao.set_run_state(run_id, "failed", finished=True)
        dao.record_audit(run_id, actor="gateway", action="run_failed", payload={"error": msg})
        bus.publish(run_id, {"kind": "state_change", "state": "failed", "error": msg})
    finally:
        if not ws_override:
            workspace.cleanup_workspace(run_id)


def _on_progress(run_id: str, p: dict) -> None:
    text = p.get("text", "")
    if text:
        bus.publish(run_id, {"kind": "agent_message", "text": text[:2000],
                             "invoked": p.get("invoked", ""), "origin": p.get("origin", [])})
    state = _derive_state(p)
    if state:
        _advance(run_id, state)


# ---------------------------------------------------------------- request models
class SimulateBody(BaseModel):
    event: dict
    repo_workspace: str | None = None  # skip clone (tests / pre-prepared repo)


_REQUIRED = [("event_id",), ("repo", "name"), ("change", "base_sha"), ("change", "head_sha"),
             ("target_transition", "from_env"), ("target_transition", "to_env")]


def _validate_event(event: dict) -> None:
    for path in _REQUIRED:
        node: Any = event
        for key in path:
            node = (node or {}).get(key) if isinstance(node, dict) else None
        if node in (None, ""):
            raise HTTPException(400, f"event missing {'.'.join(path)}")


_TASKS: set[asyncio.Task] = set()  # keep strong refs so background runs aren't GC'd


def _start_run(event: dict, ws_override: str | None) -> str:
    tt = event["target_transition"]
    run_id = str(uuid.uuid4())
    stored = copy.deepcopy(event)  # H2: DB/API copy with credentials stripped from repo.url
    repo = stored.get("repo") or {}
    if repo.get("url"):
        repo["url"] = _redact_url(repo["url"])
    dao.insert_run(run_id, event=stored, source=event.get("source", "manual"),
                   repo=event["repo"]["name"], from_env=tt["from_env"], to_env=tt["to_env"])
    dao.record_audit(run_id, actor="gateway", action="run_received",
                     payload={"event_id": event["event_id"]})
    task = asyncio.create_task(_run_pipeline(run_id, event, ws_override))  # REAL event -> clone
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return run_id


# ---------------------------------------------------------------- endpoints
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "open_mode": settings.OPEN_MODE}  # open_mode drives the SPA login gate


@app.get("/api/v1/whoami")
def whoami(request: Request, response: Response, role: str = Depends(_require("viewer"))) -> dict:
    """Verify a token and return its role (401 if invalid). SPA login gate calls this.

    Also drops the token in an HttpOnly cookie so the SSE stream authenticates without a URL token
    (EventSource can't send an Authorization header). Secure flag follows the request scheme.
    """
    tok = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if tok:  # not OPEN_MODE
        response.set_cookie("sentinel_token", tok, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
    return {"role": role, "open_mode": settings.OPEN_MODE}


@app.post("/api/v1/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("sentinel_token", path="/")
    return {"ok": True}


@app.post("/api/v1/simulate", status_code=202)
async def simulate(body: SimulateBody, _role: str = Depends(_require("admin"))) -> dict:
    event = body.event
    _validate_event(event)
    if settings.ALLOWED_REPOS and event["repo"]["name"] not in settings.ALLOWED_REPOS:
        raise HTTPException(403, "repo not on the Sentinel allow-list")  # C3a
    existing = dao.find_run_by_event_id(event["event_id"])
    if existing:  # idempotent intake — same event_id returns the prior run
        run = dao.get_run(existing) or {}
        return {"run_id": existing, "state": run.get("state"), "idempotent": True}
    run_id = _start_run(event, body.repo_workspace)
    return {"run_id": run_id, "state": "received"}


@app.get("/api/v1/runs")
def list_runs(repo: str | None = None, band: str | None = None, decision: str | None = None,
              state: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
              _role: str = Depends(_require("viewer"))) -> dict:
    rows = dao.list_runs(repo=repo, band=band, decision=decision, state=state,
                         limit=size, offset=(page - 1) * size)
    return {"runs": rows, "page": page, "size": size}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str, _role: str = Depends(_require("viewer"))) -> dict:
    run = dao.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    error = None
    if run.get("state") == "failed":  # surface the durable run_failed reason (audit log)
        error = next((a["payload"].get("error") for a in dao.list_audit(run_id)
                      if a.get("action") == "run_failed" and a.get("payload")), None)
    return {
        "run": run,
        "review_report": (dao.get_payload("review_reports", run_id) or {}).get("payload"),
        "test_plan": (dao.get_payload("test_plans", run_id) or {}).get("payload"),
        "test_results": (dao.get_payload("test_results", run_id) or {}).get("payload"),
        "env_context": (dao.get_payload("env_contexts", run_id) or {}).get("payload"),
        "risk_score": (dao.get_payload("risk_scores", run_id) or {}).get("payload"),
        "decision": dao.get_decision(run_id),
        "error": error,
    }


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    # EventSource can't send an Authorization header, so it rides the HttpOnly cookie set by whoami
    # (same-origin). Header fallback kept for curl/tests.
    tok = request.cookies.get("sentinel_token") \
        or (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    _check(_role_for(tok), "viewer")
    if not dao.get_run(run_id):
        raise HTTPException(404, "run not found")

    async def gen():
        q = bus.subscribe(run_id)
        seen = 0
        try:
            for ev in bus.replay(run_id):  # durable replay of progress so far
                seen = ev["seq"]
                yield {"data": json.dumps(ev)}
                if _terminal(ev):
                    return
            while True:
                ev = await q.get()
                if ev["seq"] <= seen:
                    continue
                seen = ev["seq"]
                yield {"data": json.dumps(ev)}
                if _terminal(ev):
                    return
        finally:
            bus.unsubscribe(run_id, q)

    return EventSourceResponse(gen())


@app.post("/api/v1/runs/{run_id}/rerun", status_code=202)
async def rerun(run_id: str, _role: str = Depends(_require("approver"))) -> dict:
    run = dao.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    new_id = _start_run(run["event"], None)  # same event, fresh run row
    return {"run_id": new_id, "state": "received", "rerun_of": run_id}


@app.get("/api/v1/approvals")
def approvals(status: str = "pending", _role: str = Depends(_require("viewer"))) -> dict:
    return {"approvals": dao.list_approvals(status)}


class ApprovalResolve(BaseModel):
    action: str  # approve | reject
    comment: str | None = None


@app.post("/api/v1/approvals/{approval_id}")
def resolve_approval(approval_id: int, body: ApprovalResolve,
                     role: str = Depends(_require("approver"))) -> dict:
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve|reject")
    if body.action == "reject" and not (body.comment or "").strip():
        raise HTTPException(400, "reject requires a comment")
    status = "approved" if body.action == "approve" else "rejected"
    row = dao.resolve_approval(approval_id, status, approver=f"role:{role}", comment=body.comment)
    if not row:
        raise HTTPException(409, "approval not found or already resolved")
    return {"id": row["id"], "run_id": row["run_id"], "status": row["status"]}


@app.get("/api/v1/audit")
def audit(run_id: str | None = None, _role: str = Depends(_require("viewer"))) -> dict:
    return {"events": dao.list_audit(run_id)}


# ---------------------------------------------------------------- SPA (06 §11)
# Serve the built dashboard from one origin (no CORS) when frontend/dist exists.
# API routes above win; everything else falls back to index.html for client-side routing.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if (_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "healthz")):
            raise HTTPException(404)
        root = _DIST.resolve()
        target = (root / full_path).resolve()
        # C1 containment: only serve a real file inside dist/; traversal/missing -> SPA index
        if target.is_file() and (target == root or root in target.parents):
            return FileResponse(target)
        return FileResponse(root / "index.html")
