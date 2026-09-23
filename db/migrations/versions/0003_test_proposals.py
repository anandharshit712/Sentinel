"""test_proposals table — generated-test proposals and their mutation evidence (09 §4 P3.8)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23

Many rows per run, one per proposed test. Holds the proposed diff, the mutation evidence behind
its verdict, and an adoption status a human sets. Nothing in the promotion chain reads this —
proposals never gate a decision. Additive; no change to existing tables.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE test_proposals (
            id BIGSERIAL PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES runs,
            target_file TEXT NOT NULL,
            target_function TEXT NOT NULL,
            test_path TEXT NOT NULL,
            test_source TEXT NOT NULL,
            verdict TEXT NOT NULL,
            mutation_score REAL,
            evaluation JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TIMESTAMPTZ DEFAULT now())"""
    )
    op.execute("CREATE INDEX test_proposals_run_idx ON test_proposals (run_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS test_proposals CASCADE")
