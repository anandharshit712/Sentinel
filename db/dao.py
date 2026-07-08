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


def get_run(run_id: str) -> dict | None:
    with get_engine().connect() as c:
        row = c.execute(select(models.runs).where(models.runs.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


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
