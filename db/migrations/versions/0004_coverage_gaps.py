"""coverage_gaps table — which changed code no test executes (09 §4 P3.3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23

finalize_run computes the gaps during the promotion run; the test-generation network runs later,
on demand, and needs them. Without this they would exist only in the finished run's sly_data.
Additive; no change to existing tables.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE coverage_gaps (run_id UUID PRIMARY KEY REFERENCES runs,
            payload JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now())"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS coverage_gaps CASCADE")
