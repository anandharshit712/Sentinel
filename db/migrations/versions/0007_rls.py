"""row-level security: one account can never read another's runs, even by direct id (12 §5)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20

The database enforces tenancy, not just the API. Every transaction carries the setting
`app.account_id` (set by db/dao.py):

  '<uuid>'  a request scoped to that account — sees only its own rows
  '*'       a system context: migrations, the startup reaper, the neuro-san coded tools (which only
            ever write under the run_id the Gateway placed in sly_data, invisible to the LLM)
  unset/''  sees nothing. A code path that forgets to set a scope fails closed.

FORCE makes the policies apply to the table owner too — the application's role owns these tables.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# tables whose rows belong to a run
PER_RUN = ["review_reports", "findings", "review_plans", "coverage_gaps", "test_proposals",
           "test_plans", "test_results", "env_contexts", "risk_scores", "decisions", "approvals",
           "outcomes", "notifications"]
# tables carrying account_id directly
DIRECT = ["runs", "identities", "repos", "llm_keys", "audit_events", "incidents"]

_FUNCS = [
    """CREATE FUNCTION app_scope_all() RETURNS bool LANGUAGE sql STABLE AS
       $$ SELECT coalesce(current_setting('app.account_id', true), '') = '*' $$""",
    """CREATE FUNCTION app_account() RETURNS uuid LANGUAGE sql STABLE AS
       $$ SELECT CASE WHEN coalesce(current_setting('app.account_id', true), '') IN ('', '*')
                      THEN NULL ELSE current_setting('app.account_id', true)::uuid END $$""",
]


# A row's account is derived, never trusted from the caller: an audit event belongs to its run's
# account; anything inserted inside an account scope belongs to that account. One place, so no
# insert site can forget it (and so RLS WITH CHECK cannot reject a correct write).
_TRIGGERS = [
    """CREATE FUNCTION fill_account_id() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF TG_TABLE_NAME = 'audit_events' AND NEW.run_id IS NOT NULL THEN
           NEW.account_id := coalesce((SELECT account_id FROM runs WHERE run_id = NEW.run_id),
                                      NEW.account_id);
         ELSIF app_account() IS NOT NULL THEN
           NEW.account_id := app_account();
         END IF;
         RETURN NEW;
       END $$""",
    "CREATE TRIGGER fill_account BEFORE INSERT ON runs FOR EACH ROW EXECUTE FUNCTION fill_account_id()",
    "CREATE TRIGGER fill_account BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION fill_account_id()",
    "CREATE TRIGGER fill_account BEFORE INSERT ON incidents FOR EACH ROW EXECUTE FUNCTION fill_account_id()",
]


def _enable(table: str, using: str) -> list[str]:
    return [f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant ON {table} USING (app_scope_all() OR {using})"]


def upgrade() -> None:
    stmts = list(_FUNCS) + _TRIGGERS
    stmts += _enable("accounts", "id = app_account()")
    for t in DIRECT:
        stmts += _enable(t, "account_id = app_account()")
    stmts += _enable("repo_keys", "EXISTS (SELECT 1 FROM repos r WHERE r.id = repo_keys.repo_id "
                                  "AND r.account_id = app_account())")
    for t in PER_RUN:
        stmts += _enable(t, f"EXISTS (SELECT 1 FROM runs r WHERE r.run_id = {t}.run_id "
                            f"AND r.account_id = app_account())")
    for s in stmts:
        op.execute(s)


def downgrade() -> None:
    for t in ["accounts", "repo_keys"] + DIRECT + PER_RUN:
        op.execute(f"DROP POLICY IF EXISTS tenant ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    for t in ("runs", "audit_events", "incidents"):
        op.execute(f"DROP TRIGGER IF EXISTS fill_account ON {t}")
    op.execute("DROP FUNCTION IF EXISTS fill_account_id()")
    op.execute("DROP FUNCTION IF EXISTS app_account()")
    op.execute("DROP FUNCTION IF EXISTS app_scope_all()")
