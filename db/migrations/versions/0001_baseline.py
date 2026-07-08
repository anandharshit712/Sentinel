"""baseline schema — 13 tables (04 §8)

Revision ID: 0001
Revises:
Create Date: 2026-07-08

Verbatim DDL from the LLD (04 §8). Tables land in the `sentinel` schema via the
connection search_path set in env.py. FK `REFERENCES runs` resolves to sentinel.runs.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement per element (psycopg3 runs a single command per execute).
_UPGRADE: list[str] = [
    """CREATE TABLE runs (
        run_id UUID PRIMARY KEY, event JSONB NOT NULL, source TEXT NOT NULL,
        repo TEXT NOT NULL, from_env TEXT NOT NULL, to_env TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'received',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ)""",
    "CREATE INDEX ON runs (repo, created_at DESC)",
    "CREATE INDEX ON runs (state)",

    """CREATE TABLE review_reports (
        run_id UUID PRIMARY KEY REFERENCES runs, payload JSONB NOT NULL,
        pr_health_score INT NOT NULL, recommendation TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now())""",

    """CREATE TABLE findings (
        id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs, finding_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('security','quality')), severity TEXT NOT NULL,
        category TEXT, file TEXT, line_start INT, line_end INT, cwe TEXT,
        payload JSONB NOT NULL, UNIQUE (run_id, finding_id))""",
    "CREATE INDEX ON findings (run_id, severity)",

    """CREATE TABLE test_plans (run_id UUID PRIMARY KEY REFERENCES runs, payload JSONB NOT NULL,
        selection_confidence TEXT, created_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE test_results (run_id UUID PRIMARY KEY REFERENCES runs, payload JSONB NOT NULL,
        passed INT, failed INT, skipped INT, timed_out BOOL DEFAULT false, duration_seconds REAL,
        created_at TIMESTAMPTZ DEFAULT now())""",
    "CREATE TABLE env_contexts (run_id UUID PRIMARY KEY REFERENCES runs, payload JSONB NOT NULL)",

    """CREATE TABLE risk_scores (
        run_id UUID PRIMARY KEY REFERENCES runs, score INT NOT NULL, band TEXT NOT NULL,
        formula_version TEXT NOT NULL, payload JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now())""",

    """CREATE TABLE decisions (
        run_id UUID PRIMARY KEY REFERENCES runs, decision TEXT NOT NULL,
        policy_version TEXT NOT NULL, rule_fired TEXT, reasoning_trail JSONB NOT NULL,
        approval_required BOOL NOT NULL, created_at TIMESTAMPTZ DEFAULT now())""",

    """CREATE TABLE approvals (
        id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs, status TEXT NOT NULL DEFAULT 'pending',
        approver TEXT, comment TEXT, resolved_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now())""",
    "CREATE INDEX ON approvals (status)",

    """CREATE TABLE incidents (
        id BIGSERIAL PRIMARY KEY, repo TEXT NOT NULL, env TEXT NOT NULL,
        kind TEXT NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL, detail JSONB)""",
    "CREATE INDEX ON incidents (repo, env, occurred_at DESC)",

    """CREATE TABLE outcomes (
        id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs,
        outcome_type TEXT NOT NULL, payload JSONB, recorded_at TIMESTAMPTZ DEFAULT now())""",

    """CREATE TABLE audit_events (
        id BIGSERIAL PRIMARY KEY, run_id UUID, actor TEXT NOT NULL,
        action TEXT NOT NULL, payload JSONB, at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE INDEX ON audit_events (run_id, at)",

    """CREATE TABLE notifications (
        id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES runs, kind TEXT, summary TEXT,
        read BOOL DEFAULT false, created_at TIMESTAMPTZ DEFAULT now())""",
]

# Drop in FK-safe order (children first); CASCADE covers indexes.
_TABLES = [
    "notifications", "audit_events", "outcomes", "incidents", "approvals",
    "decisions", "risk_scores", "env_contexts", "test_results", "test_plans",
    "findings", "review_reports", "runs",
]


def upgrade() -> None:
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS " + ", ".join(_TABLES) + " CASCADE")
