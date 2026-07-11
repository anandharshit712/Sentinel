"""SQLAlchemy Core table definitions — mirror the DDL baseline (04 §8).

Tables are created by Alembic (migrations/), NOT by this module; these definitions exist
so the DAO and Gateway can build typed queries. All tables live in the `sentinel` schema.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Float, Integer, MetaData, Table, Text, DateTime,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

metadata = MetaData(schema="sentinel")

_UUID = UUID(as_uuid=False)  # run_id round-trips as plain strings (matches contracts)
_TS = DateTime(timezone=True)

runs = Table(
    "runs", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("event", JSONB, nullable=False),
    Column("source", Text, nullable=False),
    Column("repo", Text, nullable=False),
    Column("from_env", Text, nullable=False),
    Column("to_env", Text, nullable=False),
    Column("state", Text, nullable=False, server_default="received"),
    Column("created_at", _TS, nullable=False, server_default=func.now()),
    Column("finished_at", _TS),
)

review_reports = Table(
    "review_reports", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("pr_health_score", Integer, nullable=False),
    Column("recommendation", Text, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

findings = Table(
    "findings", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", _UUID),
    Column("finding_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("category", Text),
    Column("file", Text),
    Column("line_start", Integer),
    Column("line_end", Integer),
    Column("cwe", Text),
    Column("payload", JSONB, nullable=False),
)

review_plans = Table(
    "review_plans", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

test_plans = Table(
    "test_plans", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("selection_confidence", Text),
    Column("created_at", _TS, server_default=func.now()),
)

test_results = Table(
    "test_results", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("passed", Integer),
    Column("failed", Integer),
    Column("skipped", Integer),
    Column("timed_out", Boolean, server_default="false"),
    Column("duration_seconds", Float),
    Column("created_at", _TS, server_default=func.now()),
)

env_contexts = Table(
    "env_contexts", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
)

risk_scores = Table(
    "risk_scores", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("score", Integer, nullable=False),
    Column("band", Text, nullable=False),
    Column("formula_version", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

decisions = Table(
    "decisions", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("rule_fired", Text),
    Column("reasoning_trail", JSONB, nullable=False),
    Column("approval_required", Boolean, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

approvals = Table(
    "approvals", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", _UUID),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("approver", Text),
    Column("comment", Text),
    Column("resolved_at", _TS),
    Column("created_at", _TS, server_default=func.now()),
)

incidents = Table(
    "incidents", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("repo", Text, nullable=False),
    Column("env", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("occurred_at", _TS, nullable=False),
    Column("detail", JSONB),
)

outcomes = Table(
    "outcomes", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", _UUID),
    Column("outcome_type", Text, nullable=False),
    Column("payload", JSONB),
    Column("recorded_at", _TS, server_default=func.now()),
)

audit_events = Table(
    "audit_events", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", _UUID),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("payload", JSONB),
    Column("at", _TS, nullable=False, server_default=func.now()),
)

notifications = Table(
    "notifications", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", _UUID),
    Column("kind", Text),
    Column("summary", Text),
    Column("read", Boolean, server_default="false"),
    Column("created_at", _TS, server_default=func.now()),
)

# name -> Table, for generic per-run payload writes
RUN_PAYLOAD_TABLES = {
    "review_reports": review_reports,
    "review_plans": review_plans,
    "test_plans": test_plans,
    "test_results": test_results,
    "env_contexts": env_contexts,
    "risk_scores": risk_scores,
    "decisions": decisions,
}
