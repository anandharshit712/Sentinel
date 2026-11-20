"""findings become rows a human can judge; runs carry the Sentinel version (12 §2 Phase 1)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20

The `findings` table has existed since the baseline and was never written. report_publisher now
writes one row per merged finding so a reviewer can mark it real / false positive / won't fix, and
precision becomes a number instead of an impression.

`kind` already holds the review dimension; its CHECK only allowed security|quality, from before the
performance/compliance reviewers and the spec-mismatch floor existed, so it is widened rather than
duplicated by a new column.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE findings DROP CONSTRAINT IF EXISTS findings_kind_check")
    op.execute("""ALTER TABLE findings ADD CONSTRAINT findings_kind_check CHECK
                  (kind IN ('security','quality','performance','compliance','spec'))""")
    op.execute("""ALTER TABLE findings
                  ADD COLUMN fingerprint TEXT,
                  ADD COLUMN source TEXT,
                  ADD COLUMN verdict TEXT CHECK (verdict IN ('real','false_positive','wont_fix')),
                  ADD COLUMN verdict_by TEXT,
                  ADD COLUMN verdict_at TIMESTAMPTZ,
                  ADD COLUMN note TEXT""")
    op.execute("CREATE INDEX findings_fingerprint_idx ON findings (fingerprint)")
    op.execute("ALTER TABLE runs ADD COLUMN sentinel_version TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS sentinel_version")
    op.execute("DROP INDEX IF EXISTS findings_fingerprint_idx")
    op.execute("""ALTER TABLE findings DROP COLUMN IF EXISTS note, DROP COLUMN IF EXISTS verdict_at,
                  DROP COLUMN IF EXISTS verdict_by, DROP COLUMN IF EXISTS verdict,
                  DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS fingerprint""")
    op.execute("ALTER TABLE findings DROP CONSTRAINT IF EXISTS findings_kind_check")
    op.execute("ALTER TABLE findings ADD CONSTRAINT findings_kind_check CHECK (kind IN ('security','quality'))")
