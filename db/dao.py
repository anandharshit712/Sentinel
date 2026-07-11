"""Thin shared DAO (07 §1.3) — one SQLAlchemy engine + high-value helpers.

Used by coded tools and the Gateway. Deliberately minimal: a run-row writer, a generic
per-run JSONB-payload upsert, the incident-history query, and an audit writer. Add typed
helpers here as tools need them — don't speculate.

Engine URL from DATABASE_URL (.env). The `sentinel` search_path comes from the role default,
and models pin schema="sentinel" explicitly, so queries are schema-qualified either way.
"""
from __future__ import annotations

import datetime as _dt
import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import models

load_dotenv()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = os.environ["DATABASE_URL"]
    return create_engine(url, pool_pre_ping=True, future=True)


def insert_run(run_id: str, event: dict, source: str, repo: str,
               from_env: str, to_env: str, state: str = "received") -> None:
    with get_engine().begin() as c:
        c.execute(models.runs.insert().values(
            run_id=run_id, event=event, source=source, repo=repo,
            from_env=from_env, to_env=to_env, state=state))


def set_run_state(run_id: str, state: str, finished: bool = False) -> None:
    vals: dict[str, Any] = {"state": state}
    if finished:
        vals["finished_at"] = _dt.datetime.now(_dt.timezone.utc)
    with get_engine().begin() as c:
        c.execute(models.runs.update().where(models.runs.c.run_id == run_id).values(**vals))


def reap_unfinished_runs() -> list[str]:
    """Fail every non-terminal run in one statement; returns the reaped run_ids.

    In-flight runs live in an in-memory asyncio task that does NOT survive a Gateway restart, so any
    row left non-terminal (received/analyzing/…/gated with no finished_at) is orphaned — no task can
    ever finalize it. Called on startup to reconcile; the runs become re-runnable via Rerun.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    with get_engine().begin() as c:
        res = c.execute(models.runs.update()
                        .where(models.runs.c.finished_at.is_(None),
                               models.runs.c.state.notin_(("done", "failed")))
                        .values(state="failed", finished_at=now)
                        .returning(models.runs.c.run_id))
        return [row[0] for row in res]


def save_run_payload(table: str, run_id: str, payload: dict, **cols: Any) -> None:
    """Upsert a per-run JSONB row (review_reports/test_plans/…), on run_id conflict."""
    t = models.RUN_PAYLOAD_TABLES.get(table)
    if t is None:
        raise KeyError(f"{table} is not a per-run payload table; known: {sorted(models.RUN_PAYLOAD_TABLES)}")
    vals = {"run_id": run_id, "payload": payload, **cols}
    stmt = pg_insert(t).values(**vals)
    stmt = stmt.on_conflict_do_update(
        index_elements=["run_id"], set_={k: v for k, v in vals.items() if k != "run_id"})
    with get_engine().begin() as c:
        c.execute(stmt)


def recent_incidents(repo: str, env: str, days: int = 7) -> dict:
    """Count + most-recent incident for repo+env within `days` (incident_history_tool)."""
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    t = models.incidents
    with get_engine().connect() as c:
        count, most_recent = c.execute(
            select(func.count(), func.max(t.c.occurred_at))
            .where(t.c.repo == repo, t.c.env == env, t.c.occurred_at > cutoff)
        ).one()
    return {"count": count, "most_recent_at": most_recent.isoformat() if most_recent else None}


def record_audit(run_id: str | None, actor: str, action: str, payload: dict | None = None) -> None:
    with get_engine().begin() as c:
        c.execute(models.audit_events.insert().values(
            run_id=run_id, actor=actor, action=action, payload=payload))


def insert_decision(run_id: str, decision: dict) -> None:
    """Persist a decision (+ a pending approval when required) + audit, in one transaction (decision_logger)."""
    with get_engine().begin() as c:
        c.execute(pg_insert(models.decisions).values(
            run_id=run_id, decision=decision["decision"], policy_version=decision["policy_version"],
            rule_fired=decision.get("rule_fired"), reasoning_trail=decision["reasoning_trail"],
            approval_required=decision["approval_required"],
        ).on_conflict_do_nothing(index_elements=["run_id"]))
        if decision.get("approval_required"):
            c.execute(models.approvals.insert().values(run_id=run_id, status="pending"))
        c.execute(models.audit_events.insert().values(
            run_id=run_id, actor="agent:promotion_gating", action="decision_logged",
            payload={"decision": decision["decision"], "rule_fired": decision.get("rule_fired")}))


def insert_notification(run_id: str | None, kind: str, summary: str) -> None:
    """Insert a dashboard notification row (notification_tool; failures are caller-handled non-fatal)."""
    with get_engine().begin() as c:
        c.execute(models.notifications.insert().values(run_id=run_id, kind=kind, summary=summary))


def get_run(run_id: str) -> dict | None:
    with get_engine().connect() as c:
        row = c.execute(select(models.runs).where(models.runs.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def find_run_by_event_id(event_id: str) -> str | None:
    """Latest run_id for a given event_id (idempotency for simulate/webhook intake)."""
    r = models.runs
    with get_engine().connect() as c:
        row = c.execute(
            select(r.c.run_id).where(r.c.event["event_id"].astext == event_id)
            .order_by(r.c.created_at.desc()).limit(1)).first()
    return row[0] if row else None


def list_runs(*, repo: str | None = None, band: str | None = None, decision: str | None = None,
              state: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    """Runs list (04 §7.2) enriched with risk band/score + decision via left joins."""
    r, rs, d = models.runs, models.risk_scores, models.decisions
    j = r.join(rs, r.c.run_id == rs.c.run_id, isouter=True).join(
        d, r.c.run_id == d.c.run_id, isouter=True)
    q = select(r.c.run_id, r.c.repo, r.c.from_env, r.c.to_env, r.c.state,
               r.c.created_at, r.c.finished_at, rs.c.score, rs.c.band,
               d.c.decision, d.c.approval_required).select_from(j)
    if repo:
        q = q.where(r.c.repo == repo)
    if state:
        q = q.where(r.c.state == state)
    if band:
        q = q.where(rs.c.band == band)
    if decision:
        q = q.where(d.c.decision == decision)
    q = q.order_by(r.c.created_at.desc()).limit(limit).offset(offset)
    with get_engine().connect() as c:
        return [dict(m) for m in c.execute(q).mappings().all()]


def get_payload(table: str, run_id: str) -> dict | None:
    """Fetch one per-run JSONB payload row (review_reports/test_results/risk_scores/…)."""
    t = models.RUN_PAYLOAD_TABLES.get(table)
    if t is None:
        raise KeyError(f"{table} is not a per-run payload table")
    with get_engine().connect() as c:
        row = c.execute(select(t).where(t.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def get_decision(run_id: str) -> dict | None:
    with get_engine().connect() as c:
        row = c.execute(select(models.decisions).where(
            models.decisions.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def list_approvals(status: str = "pending") -> list[dict]:
    a = models.approvals
    with get_engine().connect() as c:
        rows = c.execute(select(a).where(a.c.status == status)
                         .order_by(a.c.created_at.desc())).mappings().all()
    return [dict(m) for m in rows]


def resolve_approval(approval_id: int, status: str, approver: str,
                     comment: str | None = None) -> dict | None:
    """Resolve a pending approval (approve|reject). Returns the updated row (with run_id) or None."""
    a = models.approvals
    with get_engine().begin() as c:
        row = c.execute(a.update().where(a.c.id == approval_id, a.c.status == "pending").values(
            status=status, approver=approver, comment=comment,
            resolved_at=_dt.datetime.now(_dt.timezone.utc)).returning(
            a.c.id, a.c.run_id, a.c.status)).mappings().first()
        if row:
            c.execute(models.audit_events.insert().values(
                run_id=row["run_id"], actor=approver, action=f"approval_{status}",
                payload={"approval_id": approval_id, "comment": comment}))
    return dict(row) if row else None


def list_audit(run_id: str | None = None, limit: int = 200) -> list[dict]:
    e = models.audit_events
    q = select(e).order_by(e.c.at.desc()).limit(limit)
    if run_id:
        q = q.where(e.c.run_id == run_id)
    with get_engine().connect() as c:
        return [dict(m) for m in c.execute(q).mappings().all()]


def demo() -> None:
    """Live round-trip against the sentinel DB, then clean up the test rows."""
    rid = "dddddddd-dead-beef-dead-dddddddddddd"
    eng = get_engine()

    def _purge():  # FK has no ON DELETE CASCADE (04 §8) — delete children before the run
        with eng.begin() as c:
            c.execute(models.review_reports.delete().where(models.review_reports.c.run_id == rid))
            c.execute(models.audit_events.delete().where(models.audit_events.c.run_id == rid))
            c.execute(models.runs.delete().where(models.runs.c.run_id == rid))

    _purge()  # clean any leftover from a prior run

    insert_run(rid, event={"event_id": "x"}, source="manual",
               repo="python-payments-service", from_env="dev", to_env="test")
    save_run_payload("review_reports", rid, payload={"executive_summary": "ok"},
                     pr_health_score=88, recommendation="approve")
    set_run_state(rid, "reviewing")
    record_audit(rid, actor="agent:test", action="smoke")

    run = get_run(rid)
    assert run and run["state"] == "reviewing" and run["repo"] == "python-payments-service", run
    inc = recent_incidents("python-payments-service", "test", days=7)
    assert set(inc) == {"count", "most_recent_at"}, inc

    _purge()
    assert get_run(rid) is None
    print(f"dao OK: run round-trip + payload upsert + incidents query + audit, incidents={inc}")


if __name__ == "__main__":
    demo()
