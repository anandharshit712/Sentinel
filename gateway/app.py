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
import logging
import os
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db import accounts, dao
from gateway import auth, settings

logger = logging.getLogger("gateway")
from gateway.invoker.neuro_san_client import invoke_network
from lib import calibration, sandbox, secretbox, untested, workspace
from lib.redact import redact

app = FastAPI(title="Sentinel Delivery Gateway", version="1")


@app.middleware("http")
async def _deny_by_default(request: Request, call_next):
    """Hosted mode: every request starts in the deny scope; only _authenticate widens it to the
    caller's account. A handler that forgets to authenticate sees no rows, rather than all of them."""
    if auth.AUTH_MODE == "oidc":
        dao.set_scope("")
    response = await call_next(request)
    new = getattr(request.state, "new_tokens", None)
    if new:   # the session was refreshed while serving this request
        _set_session_cookies(response, new)
    return response


def _set_session_cookies(response: Response, tokens: dict) -> None:
    secure = auth.PUBLIC_URL.startswith("https")
    response.set_cookie("sentinel_session", tokens["access_token"], httponly=True, samesite="lax",
                        secure=secure, max_age=int(tokens.get("expires_in", 300)), path="/")
    if tokens.get("refresh_token"):
        response.set_cookie("sentinel_refresh", tokens["refresh_token"], httponly=True, samesite="lax",
                            secure=secure, max_age=int(tokens.get("refresh_expires_in", 28800)), path="/")


@app.on_event("startup")
async def _retention_loop() -> None:
    """Hosted mode: purge runs past each account's retention (default 90 days) once a day. Not in
    token mode — a self-hosted install's history is its operator's to keep."""
    if auth.AUTH_MODE != "oidc":
        return

    async def loop() -> None:
        while True:
            try:
                purged = await asyncio.to_thread(accounts.purge_expired)
                if purged:
                    logger.info("retention purge: %s", purged)
            except Exception as e:   # a failed purge must not take the Gateway down
                logger.error("retention purge failed: %s", e)
            await asyncio.sleep(24 * 3600)

    _TASKS.add(asyncio.create_task(loop()))


@app.on_event("startup")
def _reap_orphaned_runs() -> None:
    """Tasks don't survive a restart, so any non-terminal run in the DB is orphaned. Fail them on
    boot (re-runnable via Rerun) instead of leaving them stale 'testing'/'received' forever."""
    for rid in dao.reap_unfinished_runs():
        dao.record_audit(rid, actor="gateway", action="run_stopped",
                         payload={"error": "interrupted by gateway restart"})

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


def _bearer(request: Request) -> str:
    return (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()


async def _authenticate(request: Request, min_role: str, allow_unlinked: bool = False) -> str:
    """Token mode: the role of a shared token (self-host, one tenant). OIDC mode: a Keycloak
    session -> its account; the request's DB scope becomes that account, so row-level security
    fences every query the handler makes. Until a code host is linked only profile/linking routes
    answer (the link gate)."""
    if auth.AUTH_MODE != "oidc":
        return _check(_role_for(_bearer(request) or request.cookies.get("sentinel_token")), min_role)
    tok = request.cookies.get("sentinel_session") or _bearer(request)
    refresh_tok = request.cookies.get("sentinel_refresh")
    if not tok and not refresh_tok:
        raise HTTPException(401, "sign in required")
    try:
        claims = auth.verify_user_token(tok or "")
    except auth.AuthError as e:
        # expired access token: renew silently from the refresh cookie (Keycloak decides whether
        # the session is still alive), then hand the new cookies back on this response
        if not refresh_tok:
            raise HTTPException(401, str(e)) from e
        try:
            tokens = await asyncio.to_thread(auth.refresh, refresh_tok)
            tok = tokens["access_token"]
            claims = auth.verify_user_token(tok)
        except (auth.AuthError, KeyError) as e2:
            raise HTTPException(401, str(e2)) from e2
        request.state.new_tokens = tokens
    acct = await asyncio.to_thread(accounts.account_for_login, claims["sub"], claims.get("email"),
                                   claims.get("name") or claims.get("preferred_username"))
    if acct["status"] != "active":
        raise HTTPException(403, "account suspended")
    dao.set_scope(acct["id"])
    request.state.account, request.state.claims, request.state.token = acct, claims, tok
    if not allow_unlinked and not await asyncio.to_thread(accounts.linked_hosts, acct["id"]):
        raise HTTPException(403, "link_required: link a GitHub or GitLab account first")
    return "admin"     # an account administers its own tenant; platform admin is accounts.is_admin


def _require(min_role: str, allow_unlinked: bool = False):
    async def dep(request: Request) -> str:
        return await _authenticate(request, min_role, allow_unlinked)
    return dep


def _account_id(request: Request) -> str:
    """The caller's account: their own in OIDC mode, the single legacy tenant in token mode."""
    acct = getattr(request.state, "account", None)
    return acct["id"] if acct else dao.LEGACY_ACCOUNT


def _require_platform_admin(request: Request) -> None:
    acct = getattr(request.state, "account", None)
    if acct is not None and not acct.get("is_admin"):
        raise HTTPException(403, "platform admin only")


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
    # core.symlinks=false: a repo symlink is checked out as a plain file, so it cannot point the
    # analysis tools (which read the tree on the host) at another run's workspace or a host file.
    r = subprocess.run(["git", "-c", "protocol.ext.allow=never", "-c", "core.longpaths=true",
                        "-c", "core.symlinks=false", "clone", "--quiet", "--", url, ws],
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
    test_plan/env_context/review_plan live only in sly_data, so the Gateway lands them for the dashboard.
    Best-effort: a missing/partial contract is skipped, never fails the run.
    """
    try:
        _do_persist_contracts(run_id, sly)
    except Exception as e:  # best-effort: a malformed contract must never fail an otherwise-green run
        logger.warning("run %s: contract persistence skipped: %s", run_id, e)


def _do_persist_contracts(run_id: str, sly: dict) -> None:
    rp = sly.get("review_plan") or {}
    if rp.get("shards") is not None:
        dao.save_run_payload("review_plans", run_id, rp)
    rs = sly.get("risk_score") or {}
    if {"score", "band", "formula_version"} <= rs.keys() and isinstance(rs.get("score"), (int, float)):
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
    cg = sly.get("coverage_gaps") or {}
    if "measured" in cg:   # `measured: false` is a real result and must be stored as one
        dao.save_run_payload("coverage_gaps", run_id, cg)


async def _run_pipeline(run_id: str, event: dict, ws_override: str | None) -> None:
    loop = asyncio.get_running_loop()

    def on_progress(p: dict) -> None:  # called from the invoker worker thread
        loop.call_soon_threadsafe(_on_progress, run_id, p)

    try:
        llm = _llm_config_for_run(run_id)      # before the clone: no key, no work
        ws = ws_override or await asyncio.to_thread(_clone, event, run_id)
        if auth.AUTH_MODE == "oidc" and not ws_override:
            mb = await asyncio.to_thread(_dir_mb, ws)
            if mb > settings.MAX_REPO_MB:
                raise RuntimeError(f"repository is {mb:.0f} MB, over the {settings.MAX_REPO_MB} MB limit")
        # per-run sandbox container (docker mode); a no-op in local mode. Refusal fails the run.
        await asyncio.to_thread(sandbox.open_run, run_id, ws, (event.get("repo") or {}).get("name"))
        _advance(run_id, "analyzing")
        structure, sly, _answer = await asyncio.to_thread(
            invoke_network, run_id, event, ws,
            host=settings.NEURO_SAN_HOST, port=settings.NEURO_SAN_PORT,
            network=settings.NEURO_SAN_NETWORK, on_progress=on_progress,
            extra_sly={"llm_config": llm} if llm else None)
        _persist_contracts(run_id, sly)
        decision = (sly.get("decision") or {}).get("decision") \
            or (dao.get_decision(run_id) or {}).get("decision")
        # A completed pipeline always ends in a promotion decision. None means the frontman
        # returned without running the pipeline (e.g. mistral answered in prose, 0 tool calls) —
        # a silent empty run. Fail it (re-runnable via RERUN) instead of showing a green DONE
        # with no report/decision. ponytail: no auto-retry; RERUN covers the occasional flake.
        if not decision:
            # Say how far it actually got: "0 agents invoked" was a guess, and it is wrong for the
            # common case — the chain runs most of the way, then a provider error (NIM 500) or
            # frontman drift drops the deterministic tail. Naming the reached contracts points at
            # which step died instead of misdirecting to the frontman.
            reached = [k for k in ("change_profile", "review_report", "test_plan", "test_results",
                                   "env_context", "risk_score") if sly.get(k)]
            raise RuntimeError("network finished without a promotion decision — the pipeline "
                               f"stopped after {', '.join(reached) if reached else 'no steps'}. "
                               "Re-run this event.")
        dao.set_run_state(run_id, "done", finished=True)
        bus.publish(run_id, {"kind": "state_change", "state": "done",
                             "decision": decision, "structure": structure})
        # Test generation for CI runs (12 §7): only for changed functions no test reaches, and
        # only AFTER the decision is persisted and published — it can never delay or alter the gate.
        if event.get("source") != "manual":
            sel = await asyncio.to_thread(untested.candidates_for_run, sly, ws,
                                          (event.get("repo") or {}).get("name"))
            if sel["candidates"]:            # none -> no model call, nothing posted
                dao.record_audit(run_id, actor="gateway", action="tests_auto_generation_started",
                                 payload={"candidates": len(sel["candidates"]),
                                          "truncated": sel.get("truncated", 0)})
                t = asyncio.create_task(_auto_generate_tests(run_id, event, sel))
                _TASKS.add(t)
                t.add_done_callback(_TASKS.discard)
    except asyncio.CancelledError:  # operator hit Stop — mark failed (re-runnable), then propagate.
        # The invoke_network worker thread can't be interrupted, but the run is finalized here so
        # the UI leaves "running" immediately; the orphaned thread's result is ignored on return.
        msg = "stopped by operator"
        dao.set_run_state(run_id, "failed", finished=True)
        dao.record_audit(run_id, actor="gateway", action="run_stopped", payload={"error": msg})
        bus.publish(run_id, {"kind": "state_change", "state": "failed", "error": msg})
        raise
    except Exception as e:  # transport/stream/clone failure -> failed, re-runnable
        msg = redact(str(e))[:500]  # M3: scrub credentials (e.g. tokenized clone url in git stderr)
        dao.set_run_state(run_id, "failed", finished=True)
        dao.record_audit(run_id, actor="gateway", action="run_failed", payload={"error": msg})
        bus.publish(run_id, {"kind": "state_change", "state": "failed", "error": msg})
    finally:
        try:
            sandbox.close_run(run_id)
        except Exception as e:   # never let teardown hide the run's real outcome
            logger.warning("run %s: sandbox teardown failed: %s", run_id, e)
        if not ws_override:
            workspace.cleanup_workspace(run_id)


async def _auto_generate_tests(run_id: str, event: dict, sel: dict) -> None:
    """Write tests for the selected untested functions on a fresh clone, in the run's own sandbox.
    Runs after the gate's own sandbox is torn down (same run_id: the coded tools only see that)."""
    ws = None
    try:
        llm = _llm_config_for_run(run_id)
        ws = await asyncio.to_thread(_clone, event, f"{run_id}-testgen")
        await asyncio.to_thread(sandbox.open_run, run_id, ws, (event.get("repo") or {}).get("name"))

        def call(extra: dict) -> None:
            invoke_network(run_id, event, ws, host=settings.NEURO_SAN_HOST, port=settings.NEURO_SAN_PORT,
                           network="sentinel_testgen", prompt=untested.PROMPT,
                           extra_sly={**extra, **({"llm_config": llm} if llm else {})})

        out = await asyncio.to_thread(untested.generate, sel, call)
        dao.record_audit(run_id, actor="gateway", action="tests_auto_generated",
                         payload={"attempted": out.get("attempted"), "truncated": out.get("truncated"),
                                  "skipped": len(sel.get("skipped") or []),
                                  "proposals": len(dao.list_test_proposals(run_id))})
    except Exception as e:   # advisory: a failure here is logged, never surfaced as a gate failure
        logger.warning("run %s: auto test generation failed: %s", run_id, e)
        dao.record_audit(run_id, actor="gateway", action="tests_auto_generated",
                         payload={"error": redact(str(e))[:300]})
    finally:
        try:
            sandbox.close_run(run_id)
        except Exception as e:
            logger.warning("run %s: sandbox teardown failed: %s", run_id, e)
        if ws:
            workspace.cleanup_workspace(f"{run_id}-testgen")


def _dir_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


def _llm_config_for_run(run_id: str) -> dict | None:
    """The LLM key this run may use, as neuro-san's sly_data.llm_config (BYOK).

    OIDC mode: the run's repo key, else its account key — never the host's. Token mode with
    LLM_KEY_SOURCE=sly_data: this install's own env key, through the same path. Otherwise None and
    the LLM configs read the env key directly, as before."""
    if auth.AUTH_MODE == "oidc":
        run = dao.get_run(run_id) or {}
        ct = accounts.llm_key_for(run.get("account_id"), run.get("repo_id"))
        if not ct:
            raise RuntimeError("no LLM key configured for this account — add one under Settings")
        return {"nvidia_api_key": secretbox.open_(ct)}
    if settings.LLM_KEY_SOURCE == "sly_data":
        return {"nvidia_api_key": os.environ.get("NVIDIA_API_KEY", "")}
    return None


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
_RUN_TASKS: dict[str, asyncio.Task] = {}  # run_id -> task, so /stop can cancel a specific run


def _start_run(event: dict, ws_override: str | None, repo_id: str | None = None) -> str:
    tt = event["target_transition"]
    run_id = str(uuid.uuid4())
    stored = copy.deepcopy(event)  # H2: DB/API copy with credentials stripped from repo.url
    repo = stored.get("repo") or {}
    if repo.get("url"):
        repo["url"] = _redact_url(repo["url"])
    # account_id is filled by the DB from the request's scope (0007 trigger), never by the caller
    dao.insert_run(run_id, event=stored, source=event.get("source", "manual"),
                   repo=event["repo"]["name"], from_env=tt["from_env"], to_env=tt["to_env"],
                   repo_id=repo_id)
    dao.record_audit(run_id, actor="gateway", action="run_received",
                     payload={"event_id": event["event_id"]})
    task = asyncio.create_task(_run_pipeline(run_id, event, ws_override))  # REAL event -> clone
    _TASKS.add(task)
    _RUN_TASKS[run_id] = task
    task.add_done_callback(lambda t: (_TASKS.discard(t), _RUN_TASKS.pop(run_id, None)))
    return run_id


# ---------------------------------------------------------------- endpoints
@app.get("/healthz")
def healthz() -> dict:
    # open_mode/auth_mode drive the SPA's login gate
    return {"status": "ok", "open_mode": settings.OPEN_MODE and auth.AUTH_MODE != "oidc",
            "auth_mode": auth.AUTH_MODE}


@app.get("/api/v1/whoami")
def whoami(request: Request, response: Response,
           role: str = Depends(_require("viewer", allow_unlinked=True))) -> dict:
    """Verify a token and return its role (401 if invalid). SPA login gate calls this.

    Also drops the token in an HttpOnly cookie so the SSE stream authenticates without a URL token
    (EventSource can't send an Authorization header). Secure flag follows the request scheme.
    """
    if auth.AUTH_MODE == "oidc":
        return {"role": role, "open_mode": False, "auth_mode": "oidc", **_me(request)}
    tok = _bearer(request)
    if tok:  # not OPEN_MODE
        response.set_cookie("sentinel_token", tok, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
    return {"role": role, "open_mode": settings.OPEN_MODE, "auth_mode": "tokens"}


@app.post("/api/v1/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("sentinel_token", path="/")
    return {"ok": True}


@app.post("/api/v1/simulate", status_code=202)
async def simulate(body: SimulateBody, request: Request, _role: str = Depends(_require("admin"))) -> dict:
    event = body.event
    _validate_event(event)
    if not body.repo_workspace and not (event.get("repo") or {}).get("url"):
        raise HTTPException(400, "event missing repo.url (required unless repo_workspace is supplied)")
    if settings.ALLOWED_REPOS and event["repo"]["name"] not in settings.ALLOWED_REPOS:
        raise HTTPException(403, "repo not on the Sentinel allow-list")  # C3a
    existing = dao.find_run_by_event_id(event["event_id"])
    if existing:  # idempotent intake — same event_id returns the prior run
        run = dao.get_run(existing) or {}
        return {"run_id": existing, "state": run.get("state"), "idempotent": True}
    repo_id = None
    if auth.AUTH_MODE == "oidc":
        # hosted: only a repo this account registered (and proved it administers) can be run
        if body.repo_workspace:
            raise HTTPException(400, "repo_workspace is not available in hosted mode")
        reg = _registered_repo_for(event, _account_id(request))
        repo_id = reg["id"]
        _enforce_quota(_account_id(request), repo_id)
    run_id = _start_run(event, body.repo_workspace, repo_id)
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
        "review_plan": (dao.get_payload("review_plans", run_id) or {}).get("payload"),
        "test_plan": (dao.get_payload("test_plans", run_id) or {}).get("payload"),
        "test_results": (dao.get_payload("test_results", run_id) or {}).get("payload"),
        "env_context": (dao.get_payload("env_contexts", run_id) or {}).get("payload"),
        "risk_score": (dao.get_payload("risk_scores", run_id) or {}).get("payload"),
        "coverage_gaps": (dao.get_payload("coverage_gaps", run_id) or {}).get("payload"),
        "test_proposals": dao.list_test_proposals(run_id),
        # one row per review finding, with its human verdict (Phase 1)
        "findings": dao.list_findings(run_id),
        "decision": dao.get_decision(run_id),
        "error": error,
    }


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    # EventSource can't send an Authorization header, so it rides the HttpOnly cookie set by whoami
    # (same-origin). Header fallback kept for curl/tests.
    await _authenticate(request, "viewer")
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


@app.post("/api/v1/runs/{run_id}/stop", status_code=202)
async def stop(run_id: str, _role: str = Depends(_require("approver"))) -> dict:
    if not dao.get_run(run_id):
        raise HTTPException(404, "run not found")
    task = _RUN_TASKS.get(run_id)
    if task is None or task.done():
        raise HTTPException(409, "run is not currently running")
    task.cancel()  # -> CancelledError in _run_pipeline -> state 'failed' (reason: stopped by operator)
    return {"run_id": run_id, "stopping": True}


@app.get("/api/v1/approvals")
def approvals(status: str = "pending", _role: str = Depends(_require("viewer"))) -> dict:
    return {"approvals": dao.list_approvals(status)}


class OutcomeBody(BaseModel):
    outcome: str                 # reverted | incident | clean | generated_test_caught_bug | missed_bug
    note: str | None = None
    proposal_id: int | None = None   # for generated_test_caught_bug
    # missed_bug: a real defect in this run's change that the review did not report
    file: str | None = None
    line: int | None = None
    category: str | None = None
    description: str | None = None


_OUTCOMES = ("reverted", "incident", "clean", "generated_test_caught_bug", "missed_bug")


@app.post("/api/v1/runs/{run_id}/outcome", status_code=201)
def record_outcome(run_id: str, body: OutcomeBody,
                   role: str = Depends(_require("approver"))) -> dict:
    """What happened after the decision (10 §2).

    This is the only evidence that settles whether a gate decision was right, and it can only come
    from outside the system — nothing here can observe a revert or an incident on its own.
    """
    if body.outcome not in _OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {list(_OUTCOMES)}")
    if body.outcome == "missed_bug" and not (body.description or "").strip():
        raise HTTPException(400, "missed_bug requires a description")
    if not dao.get_run(run_id):
        raise HTTPException(404, "run not found")
    payload = {"note": body.note, "recorded_by": role}
    if body.proposal_id is not None:
        payload["proposal_id"] = body.proposal_id
    if body.outcome == "missed_bug":
        payload.update(file=body.file, line=body.line, category=body.category,
                       description=body.description)
    oid = dao.record_outcome(run_id, body.outcome, payload)
    dao.record_audit(run_id, actor=role, action=f"outcome_{body.outcome}", payload=payload)
    return {"outcome_id": oid, "run_id": run_id, "outcome": body.outcome}


@app.get("/api/v1/calibration")
def calibration_report(_role: str = Depends(_require("viewer"))) -> dict:
    """Was the gate right? Rates per band with their sample sizes, plus recommendations.

    Nothing here changes a threshold — the payload says so itself (`applies_changes: false`).
    """
    return calibration.report(
        dao.list_decisions_with_band(),
        dao.list_outcomes(),
        dao.list_all_approvals(),
        dao.list_test_proposals(limit=1000),
        dao.list_finding_rows_for_precision(),
    )


class VerdictBody(BaseModel):
    verdict: str                 # real | false_positive | wont_fix
    note: str | None = None


_VERDICTS = ("real", "false_positive", "wont_fix")


@app.post("/api/v1/findings/{finding_row_id}/verdict")
def finding_verdict(finding_row_id: int, body: VerdictBody,
                    role: str = Depends(_require("approver"))) -> dict:
    """A human's judgement of one finding — the ground truth review precision is computed from.
    It never changes the run's report, score or decision."""
    if body.verdict not in _VERDICTS:
        raise HTTPException(400, f"verdict must be one of {list(_VERDICTS)}")
    row = dao.set_finding_verdict(finding_row_id, body.verdict, by=role,
                                  note=(body.note or "").strip() or None)
    if not row:
        raise HTTPException(404, "finding not found")
    return row


class ProposalAction(BaseModel):
    action: str  # adopt | discard


@app.post("/api/v1/runs/{run_id}/generate-tests", status_code=200)
async def generate_tests(run_id: str, _role: str = Depends(_require("approver"))) -> dict:
    """Write tests for this run's coverage gaps (09 §2).

    Deliberately synchronous and off the promotion path: this runs AFTER a run has finished, never
    gates anything, and writes nothing into the source repository. The run's workspace is deleted
    when the run ends, so the repo is cloned again here and removed afterwards.
    """
    run = dao.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    gaps = (dao.get_payload("coverage_gaps", run_id) or {}).get("payload") or {}
    if not [g for g in (gaps.get("gaps") or []) if g.get("status") != "covered"]:
        return {"run_id": run_id, "proposals": [], "reason": (
            "no coverage was measured for this run" if gaps.get("measured") is False
            else "no uncovered changed code to write tests for")}

    event = run.get("event") or {}
    ws = await asyncio.to_thread(_clone, event, f"{run_id}-testgen")
    try:
        # keyed by run_id: the coded tools only ever see sly_data["run_id"]
        await asyncio.to_thread(sandbox.open_run, run_id, ws, (event.get("repo") or {}).get("name"))
        last_error = None
        # One retry: generation is advisory, and the provider returns intermittent 500s that kill
        # a run mid-loop (see docs/model-evaluation.md §8). Retrying a non-gating flow is cheap;
        # retrying the promotion chain would not be.
        llm = _llm_config_for_run(run_id)
        for attempt in (1, 2):
            try:
                await asyncio.to_thread(
                    invoke_network, run_id, event, ws,
                    host=settings.NEURO_SAN_HOST, port=settings.NEURO_SAN_PORT,
                    network="sentinel_testgen",
                    prompt="Write a test for the highest-priority coverage gap in this run.",
                    # gap_context reads the workspace for the rest
                    extra_sly={"coverage_gaps": gaps, **({"llm_config": llm} if llm else {})})
                last_error = None
                break
            except Exception as e:
                last_error = str(e)[:300]
                logger.warning("run %s: test generation attempt %d failed: %s", run_id, attempt, last_error)
        proposals = dao.list_test_proposals(run_id)
        dao.record_audit(run_id, actor="gateway", action="tests_generated",
                         payload={"proposals": len(proposals), "error": last_error})
        return {"run_id": run_id, "proposals": proposals, "error": last_error}
    finally:
        try:
            sandbox.close_run(run_id)
        except Exception as e:
            logger.warning("run %s: sandbox teardown failed: %s", run_id, e)
        workspace.cleanup_workspace(f"{run_id}-testgen")


@app.post("/api/v1/proposals/{proposal_id}")
def resolve_proposal(proposal_id: int, body: ProposalAction,
                     role: str = Depends(_require("approver"))) -> dict:
    """Adopt or discard a generated test. Adopting records the decision — it does not commit the
    test; the diff is the developer's to apply (09 §2)."""
    if body.action not in ("adopt", "discard"):
        raise HTTPException(400, "action must be adopt or discard")
    status = "adopted" if body.action == "adopt" else "discarded"
    if not dao.set_proposal_status(proposal_id, status):
        raise HTTPException(404, "proposal not found")
    dao.record_audit(None, actor=role, action=f"proposal_{status}", payload={"proposal_id": proposal_id})
    return {"proposal_id": proposal_id, "status": status}


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


# ---------------------------------------------------------------- hosted sign-in (OIDC, BFF)
# The SPA never holds a token: the Gateway runs the code flow and keeps the Keycloak access token
# in an HttpOnly cookie. When it expires the SPA sends the user to /auth/login again, which is
# silent while the Keycloak SSO session lives.
def _oidc_only() -> None:
    if auth.AUTH_MODE != "oidc":
        raise HTTPException(404)


@app.get("/auth/login", include_in_schema=False)
def auth_login() -> RedirectResponse:
    _oidc_only()
    state = uuid.uuid4().hex
    r = RedirectResponse(auth.login_url(state))
    r.set_cookie("sentinel_oauth_state", state, httponly=True, samesite="lax", max_age=600, path="/")
    return r


@app.get("/auth/callback", include_in_schema=False)
def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    _oidc_only()
    if not state or not hmac.compare_digest(state, request.cookies.get("sentinel_oauth_state") or ""):
        raise HTTPException(400, "login state mismatch — start again")
    try:
        tokens = auth.exchange_code(code)
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from e
    r = RedirectResponse(auth.PUBLIC_URL + "/")
    _set_session_cookies(r, tokens)
    r.delete_cookie("sentinel_oauth_state", path="/")
    return r


@app.get("/auth/logout", include_in_schema=False)
def auth_logout() -> RedirectResponse:
    _oidc_only()
    r = RedirectResponse(auth.logout_url())
    r.delete_cookie("sentinel_session", path="/")
    r.delete_cookie("sentinel_refresh", path="/")
    return r


@app.get("/auth/link/{provider}", include_in_schema=False)
async def auth_link(provider: str, request: Request,
                    _r: str = Depends(_require("viewer", allow_unlinked=True))) -> RedirectResponse:
    _oidc_only()
    if provider not in auth.HOSTS:
        raise HTTPException(404)
    return RedirectResponse(auth.link_url(request.state.claims, provider,
                                          f"{auth.PUBLIC_URL}/auth/linked/{provider}"))


@app.get("/auth/linked/{provider}", include_in_schema=False)
async def auth_linked(provider: str, request: Request,
                      _r: str = Depends(_require("viewer", allow_unlinked=True))) -> RedirectResponse:
    _oidc_only()
    await asyncio.to_thread(_refresh_links, request)
    return RedirectResponse(auth.PUBLIC_URL + "/")


def _refresh_links(request: Request) -> dict:
    """Reconcile the account's code-host links with what Keycloak holds. A link that disappeared
    (unlinked in Keycloak directly) suspends its repos now, not at the next CI run."""
    acct, tok = request.state.account, request.state.token
    with dao.scoped(acct["id"]):
        known = accounts.linked_hosts(acct["id"])
        errors = {}
        for provider in auth.HOSTS:
            host_tok = auth.broker_token(tok, provider)
            if host_tok:
                try:
                    uid, name = auth.host_user(provider, host_tok)
                    accounts.link_identity(acct["id"], provider, uid, name)
                except (auth.AuthError, accounts.Conflict) as e:
                    errors[provider] = str(e)
            elif provider in known:
                accounts.unlink(acct["id"], provider)
        return {"links": accounts.linked_hosts(acct["id"]), "errors": errors}


def _me(request: Request) -> dict:
    acct = request.state.account
    links = _refresh_links(request)
    return {"account": {k: acct[k] for k in ("id", "email", "display_name", "is_admin", "retention_days")},
            "links": {p: {"username": i["username"], "linked_at": i["linked_at"]}
                      for p, i in links["links"].items()},
            "link_errors": links["errors"], "link_required": not links["links"]}


@app.post("/api/v1/me/links/{provider}/unlink")
async def unlink_host(provider: str, request: Request,
                      _r: str = Depends(_require("viewer", allow_unlinked=True))) -> dict:
    """Unlink a code host: Keycloak forgets the stored token, and every repo registered through it
    is suspended with its CI keys revoked, in one step."""
    _oidc_only()
    if provider not in auth.HOSTS:
        raise HTTPException(404)
    try:
        await asyncio.to_thread(auth.remove_federated_identity, request.state.claims["sub"], provider)
    except auth.AuthError as e:
        raise HTTPException(502, str(e)) from e
    suspended = await asyncio.to_thread(accounts.unlink, _account_id(request), provider)
    dao.record_audit(None, actor=_account_id(request), action=f"unlinked_{provider}",
                     payload={"repos_suspended": suspended})
    return {"unlinked": provider, "repos_suspended": suspended}


# ---------------------------------------------------------------- repos + CI keys
class RepoBody(BaseModel):
    url: str            # https://github.com/org/repo, https://gitlab.com/group/project, or any https git URL


def _host_of(url: str) -> tuple[str, str]:
    """(host kind, owner/name path) of a repo URL."""
    p = urlsplit(url)
    path = p.path.strip("/").removesuffix(".git")
    host = (p.hostname or "").lower()
    if host == "github.com":
        return "github", path
    if host == urlsplit(auth.GITLAB_URL).hostname:
        return "gitlab", path
    return "other", path


@app.get("/api/v1/repos")
def list_repos(request: Request, _r: str = Depends(_require("viewer"))) -> dict:
    out = []
    for r in accounts.list_repos(_account_id(request)):
        r = {k: v for k, v in r.items() if k != "verify_token"} | \
            ({"verify_token": r["verify_token"]} if r["status"] == "pending" else {})
        r["keys"] = accounts.list_repo_keys(r["id"])
        out.append(r)
    return {"repos": out}


@app.post("/api/v1/repos", status_code=201)
async def register_repo(body: RepoBody, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    """Register a repo after proving the caller administers it on its host.

    GitHub/GitLab: asked directly with the linked account's token. Any other host: the repo stays
    `pending` until a `.sentinel-verify` file containing the returned token is on its default branch.
    Token mode has no linked identities; the operator holding an admin token is trusted."""
    url = body.url.strip()
    try:
        _validate_clone_url(url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    kind, path = _host_of(url)
    aid = _account_id(request)
    try:
        if auth.AUTH_MODE != "oidc":
            # the operator is trusted; for a public GitHub/GitLab repo still record the host's
            # immutable id (anonymous lookup) so CI OIDC job tokens can be matched to it
            info = None
            if kind in auth.HOSTS:
                try:
                    info = await asyncio.to_thread(auth.repo_ownership, kind, "", path)
                except auth.AuthError:
                    info = None      # private or unreachable: API-key CI only
            repo = accounts.create_repo(aid, host=kind, full_name=info["full_name"] if info else path,
                                        clone_url=info["clone_url"] if info else url,
                                        host_repo_id=info["host_repo_id"] if info else None,
                                        status="active", verified_via="operator")
        elif kind in auth.HOSTS:
            host_tok = await asyncio.to_thread(auth.broker_token, request.state.token, kind)
            if not host_tok:
                raise HTTPException(403, f"link your {kind} account first")
            info = await asyncio.to_thread(auth.repo_ownership, kind, host_tok, path)
            if not info["owner"]:
                raise HTTPException(403, f"your {kind} account does not administer {path} "
                                         f"({'admin' if kind == 'github' else 'Maintainer'} access required)")
            repo = accounts.create_repo(aid, host=kind, full_name=info["full_name"], clone_url=info["clone_url"],
                                        host_repo_id=info["host_repo_id"], status="active",
                                        verified_via=info["via"])
        else:
            repo = accounts.create_repo(aid, host="other", full_name=path, clone_url=url, host_repo_id=None,
                                        status="pending", verified_via=None,
                                        verify_token="sentinel-verify-" + uuid.uuid4().hex)
    except auth.AuthError as e:
        raise HTTPException(403, str(e)) from e
    except accounts.Conflict as e:
        raise HTTPException(409, str(e)) from e
    dao.record_audit(None, actor=aid, action="repo_registered", payload={"repo_id": repo["id"], "url": repo["clone_url"]})
    return repo


@app.post("/api/v1/repos/{repo_id}/verify")
async def verify_repo(repo_id: str, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    """Check `.sentinel-verify` on the default branch of a pending (non-GitHub/GitLab) repo."""
    repo = accounts.get_repo(repo_id)
    if not repo:
        raise HTTPException(404, "repo not found")
    if repo["status"] != "pending":
        return {"id": repo_id, "status": repo["status"]}
    found = await asyncio.to_thread(_read_verify_file, repo["clone_url"], repo_id)
    if (found or "").strip() != repo["verify_token"]:
        raise HTTPException(409, "`.sentinel-verify` on the default branch does not contain the token yet")
    accounts.activate_repo(repo_id, "verify_file")
    return {"id": repo_id, "status": "active"}


def _read_verify_file(url: str, repo_id: str) -> str | None:
    ws = None
    try:
        ws = _clone({"repo": {"url": url}}, f"verify-{repo_id}")
        f = Path(ws) / ".sentinel-verify"
        return f.read_text(encoding="utf-8", errors="replace")[:200] if f.is_file() else None
    except Exception:
        return None
    finally:
        workspace.cleanup_workspace(f"verify-{repo_id}")


@app.post("/api/v1/repos/{repo_id}/keys", status_code=201)
def create_repo_key(repo_id: str, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    """A per-repo API key for CI platforms without OIDC (Jenkins). Shown once, stored hashed."""
    repo = accounts.get_repo(repo_id)
    if not repo or repo["status"] != "active":
        raise HTTPException(404, "no active repo with that id")
    kid, key = accounts.create_repo_key(repo_id)
    dao.record_audit(None, actor=_account_id(request), action="repo_key_created", payload={"repo_id": repo_id, "key_id": kid})
    return {"id": kid, "key": key, "note": "Store this now — it is not shown again."}


@app.delete("/api/v1/repos/{repo_id}/keys/{key_id}")
def revoke_repo_key(repo_id: str, key_id: int, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    if not accounts.get_repo(repo_id) or not accounts.revoke_repo_key(key_id, repo_id):
        raise HTTPException(404, "key not found or already revoked")
    dao.record_audit(None, actor=_account_id(request), action="repo_key_revoked", payload={"key_id": key_id})
    return {"revoked": key_id}


def _registered_repo_for(event: dict, account_id: str) -> dict:
    url = accounts.normalize_url((event.get("repo") or {}).get("url") or "")
    for r in accounts.list_repos(account_id):
        if r["clone_url"] == url:
            if r["status"] != "active":
                raise HTTPException(403, f"repo is {r['status']}")
            return r
    raise HTTPException(403, "repo is not registered to this account")


def _enforce_quota(account_id: str, repo_id: str | None) -> None:
    if accounts.runs_since(account_id=account_id) >= settings.QUOTA_RUNS_PER_DAY_ACCOUNT:
        raise HTTPException(429, f"account quota reached ({settings.QUOTA_RUNS_PER_DAY_ACCOUNT} runs / 24 h)")
    if repo_id and accounts.runs_since(repo_id=repo_id) >= settings.QUOTA_RUNS_PER_DAY_REPO:
        raise HTTPException(429, f"repo quota reached ({settings.QUOTA_RUNS_PER_DAY_REPO} runs / 24 h)")


# ---------------------------------------------------------------- CI intake
def _ci_repo(request: Request) -> dict:
    """The registered repo a CI call speaks for: an OIDC job token (GitHub Actions, GitLab CI) or a
    per-repo API key (Jenkins). Neither is a user session."""
    key = (request.headers.get("x-sentinel-key") or "").strip()
    if key:
        repo = accounts.repo_for_key(key)
        if not repo:
            raise HTTPException(401, "unknown or revoked API key")
    else:
        tok = _bearer(request)
        if not tok:
            raise HTTPException(401, "CI token or X-Sentinel-Key required")
        try:
            claim = auth.verify_ci_token(tok)
        except auth.AuthError as e:
            raise HTTPException(401, str(e)) from e
        repo = accounts.find_repo_by_host_id(claim["host"], claim["host_repo_id"])
        if not repo:
            raise HTTPException(403, f"{claim['path']} is not registered with Sentinel")
    if repo["status"] != "active":
        raise HTTPException(403, f"repo is {repo['status']}")
    acct = accounts.get_account(repo["account_id"])
    if not acct or acct["status"] != "active":
        raise HTTPException(403, "account suspended")
    dao.set_scope(repo["account_id"])
    return repo


class CiBody(BaseModel):
    event: dict


@app.post("/api/v1/ci/runs", status_code=202)
async def ci_run(body: CiBody, request: Request) -> dict:
    repo = _ci_repo(request)
    event = body.event
    _validate_event(event)
    if accounts.normalize_url((event.get("repo") or {}).get("url") or "") != repo["clone_url"]:
        raise HTTPException(403, "event.repo.url does not match the repo this credential belongs to")
    existing = dao.find_run_by_event_id(event["event_id"])
    if existing:
        return {"run_id": existing, "state": (dao.get_run(existing) or {}).get("state"), "idempotent": True}
    if auth.AUTH_MODE == "oidc":
        _enforce_quota(repo["account_id"], repo["id"])
    run_id = _start_run(event, None, repo["id"])
    return {"run_id": run_id, "state": "received"}


@app.get("/api/v1/ci/runs/{run_id}")
def ci_run_status(run_id: str, request: Request) -> dict:
    repo = _ci_repo(request)
    run = dao.get_run(run_id)
    if not run or run.get("repo_id") != repo["id"]:
        raise HTTPException(404, "run not found")
    report = (dao.get_payload("review_reports", run_id) or {}).get("payload") or {}
    actions = {a["action"] for a in dao.list_audit(run_id)}
    gen = ("done" if "tests_auto_generated" in actions
           else "running" if "tests_auto_generation_started" in actions
           else "none" if run["state"] in ("done", "failed") else "pending")
    proposals = [{k: p.get(k) for k in ("target_file", "target_function", "test_path", "verdict",
                                       "mutation_score", "test_source")}
                 | {"classification": (p.get("evaluation") or {}).get("classification")}
                 for p in dao.list_test_proposals(run_id)]
    return {"run_id": run_id, "state": run["state"], "decision": dao.get_decision(run_id),
            "test_generation": gen, "test_proposals": proposals,
            "review": {k: report.get(k) for k in ("pr_health_score", "recommendation", "counts",
                                                  "executive_summary")},
            "url": f"{auth.PUBLIC_URL}/runs/{run_id}"}


# ---------------------------------------------------------------- BYOK
class LlmKeyBody(BaseModel):
    key: str
    repo_id: str | None = None      # null = account-wide


@app.get("/api/v1/llm-keys")
def list_llm_keys(request: Request, _r: str = Depends(_require("viewer"))) -> dict:
    return {"keys": accounts.list_llm_keys(_account_id(request))}


@app.post("/api/v1/llm-keys", status_code=201)
async def save_llm_key(body: LlmKeyBody, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    """Store an NVIDIA NIM key, after one real tool-call request proves it works. The key is
    encrypted at rest and never returned."""
    key = body.key.strip()
    if body.repo_id and not accounts.get_repo(body.repo_id):
        raise HTTPException(404, "repo not found")
    from scripts.probe_models import config_model_ids, probe
    ok, detail = await asyncio.to_thread(probe, config_model_ids()[0], key)
    if not ok:
        raise HTTPException(400, f"key did not work against the primary model: {detail}")
    kid = accounts.save_llm_key(_account_id(request), body.repo_id, secretbox.seal(key), secretbox.hint(key))
    dao.record_audit(None, actor=_account_id(request), action="llm_key_saved",
                     payload={"key_id": kid, "repo_id": body.repo_id})
    return {"id": kid, "hint": secretbox.hint(key)}


@app.delete("/api/v1/llm-keys/{key_id}")
def revoke_llm_key(key_id: int, request: Request, _r: str = Depends(_require("admin"))) -> dict:
    """Revoke a stored key; runs that would have used it fail until another is saved."""
    if not accounts.revoke_llm_key(_account_id(request), key_id):
        raise HTTPException(404, "key not found or already revoked")
    dao.record_audit(None, actor=_account_id(request), action="llm_key_revoked", payload={"key_id": key_id})
    return {"revoked": key_id}


# ---------------------------------------------------------------- platform admin
@app.get("/api/v1/admin/accounts")
def admin_accounts(request: Request, _r: str = Depends(_require("admin"))) -> dict:
    _require_platform_admin(request)
    rows = accounts.list_accounts()
    for a in rows:
        a["runs_24h"] = accounts.runs_since(account_id=a["id"])
    return {"accounts": rows}


class AccountStatusBody(BaseModel):
    status: str     # active | suspended


@app.post("/api/v1/admin/accounts/{account_id}/status")
def admin_set_status(account_id: str, body: AccountStatusBody, request: Request,
                     _r: str = Depends(_require("admin"))) -> dict:
    """Suspend or reactivate an account. Checked on every request, so it takes effect at once."""
    _require_platform_admin(request)
    if body.status not in ("active", "suspended"):
        raise HTTPException(400, "status must be active|suspended")
    if account_id == _account_id(request):
        raise HTTPException(400, "you cannot change your own status")
    if not accounts.set_account_status(account_id, body.status):
        raise HTTPException(404, "account not found")
    dao.record_audit(None, actor=_account_id(request), action=f"account_{body.status}",
                     payload={"account_id": account_id})
    return {"id": account_id, "status": body.status}


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
