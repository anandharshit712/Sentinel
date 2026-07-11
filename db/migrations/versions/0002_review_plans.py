"""review_plans table — adaptive security-review fan-out plan (04 §8, §5.18)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-11

Persists the review_plan contract (shard sizing + metrics) so the run-detail API can explain
"why N security reviewers ran". Additive; no change to existing tables.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE review_plans (run_id UUID PRIMARY KEY REFERENCES runs, payload JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now())"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS review_plans CASCADE")
