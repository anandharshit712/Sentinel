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
    Column("sentinel_version", Text),   # git SHA of the code that judged this run (0005)
    Column("account_id", _UUID),        # tenant (0006); defaults to the legacy account in the DB
    Column("repo_id", _UUID),           # registered repo, when the run came through CI intake
)

# ---- tenancy (0006) — every row below is also fenced by row-level security (0007) ----
accounts = Table(
    "accounts", metadata,
    Column("id", _UUID, primary_key=True),
    Column("email", Text),
    Column("display_name", Text),
    Column("status", Text, nullable=False, server_default="active"),   # active | suspended
    Column("is_admin", Boolean, nullable=False, server_default="false"),
    Column("retention_days", Integer, nullable=False, server_default="90"),
    Column("created_at", _TS, server_default=func.now()),
)

identities = Table(
    "identities", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("account_id", _UUID, nullable=False),
    Column("provider", Text, nullable=False),        # keycloak | github | gitlab
    Column("subject", Text, nullable=False),         # provider's immutable user id
    Column("username", Text),
    Column("linked_at", _TS, server_default=func.now()),
    Column("unlinked_at", _TS),
)

repos = Table(
    "repos", metadata,
    Column("id", _UUID, primary_key=True),
    Column("account_id", _UUID, nullable=False),
    Column("host", Text, nullable=False),            # github | gitlab | other
    Column("host_repo_id", Text),                    # immutable host id; null for 'other'
    Column("full_name", Text, nullable=False),
    Column("clone_url", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),  # pending | active | suspended
    Column("verified_via", Text),                    # admin | maintainer | verify_file
    Column("verify_token", Text),
    Column("created_at", _TS, server_default=func.now()),
)

repo_keys = Table(
    "repo_keys", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("repo_id", _UUID, nullable=False),
    Column("key_hash", Text, nullable=False),        # sha256 of the key; the key itself is shown once
    Column("prefix", Text, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
    Column("revoked_at", _TS),
)

llm_keys = Table(
    "llm_keys", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("account_id", _UUID, nullable=False),
    Column("repo_id", _UUID),                        # null = account-wide key
    Column("provider", Text, nullable=False, server_default="nvidia"),
    Column("ciphertext", Text, nullable=False),      # Fernet, key-encryption key SENTINEL_SECRET_KEY
    Column("hint", Text),                            # last 4 characters, for the UI
    Column("validated_at", _TS),
    Column("created_at", _TS, server_default=func.now()),
    Column("revoked_at", _TS),
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
    Column("kind", Text, nullable=False),            # review dimension: security|quality|performance|compliance|spec
    Column("severity", Text, nullable=False),
    Column("category", Text),
    Column("file", Text),
    Column("line_start", Integer),
    Column("line_end", Integer),
    Column("cwe", Text),
    Column("payload", JSONB, nullable=False),
    # 0005: a human's judgement of the finding — the only ground truth for review precision.
    Column("fingerprint", Text),                     # stable across reruns of the same repo
    Column("source", Text),                          # tool | llm
    Column("verdict", Text),                         # null | real | false_positive | wont_fix
    Column("verdict_by", Text),
    Column("verdict_at", _TS),
    Column("note", Text),
)

review_plans = Table(
    "review_plans", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

# Generated-test proposals (09 §4 P3.8). Unlike the per-run contract tables this is
# MANY rows per run — one per proposed test — and it is never written by the promotion chain:
# proposals are advisory and adopting one is a human action.
coverage_gaps = Table(
    "coverage_gaps", metadata,
    Column("run_id", _UUID, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("created_at", _TS, server_default=func.now()),
)

test_proposals = Table(
    "test_proposals", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", _UUID, nullable=False),
    Column("target_file", Text, nullable=False),
    Column("target_function", Text, nullable=False),
    Column("test_path", Text, nullable=False),
    Column("test_source", Text, nullable=False),
    Column("verdict", Text, nullable=False),          # accepted | rejected | inconclusive
    Column("mutation_score", Float),
    Column("evaluation", JSONB, nullable=False),      # caught/missed mutants, threshold, reason
    Column("status", Text, nullable=False),           # proposed | adopted | discarded
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
    Column("account_id", _UUID),      # 0006
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
    Column("account_id", _UUID),      # usage log per account (0006)
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
# Every per-run JSONB table the DAO may read or write by name. A table missing from here is not a
# loud failure at import: it is a KeyError at the moment something first tries to persist or read
# it, which is usually a live run. tests/test_db.py guards it against the table definitions above.
RUN_PAYLOAD_TABLES = {
    "review_reports": review_reports,
    "coverage_gaps": coverage_gaps,
    "review_plans": review_plans,
    "test_plans": test_plans,
    "test_results": test_results,
    "env_contexts": env_contexts,
    "risk_scores": risk_scores,
    "decisions": decisions,
}
