"""accounts, linked identities, registered repos, CI keys, BYOK LLM keys (12 §5 Phase 4)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20

Every existing run is backfilled to one `legacy` account, which is also the column default, so a
self-hosted install in token mode (and the headless verify scripts) keeps working unchanged: it
simply is one tenant.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY = "00000000-0000-0000-0000-000000000001"

_UP = [
    """CREATE TABLE accounts (
        id UUID PRIMARY KEY, email TEXT, display_name TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
        is_admin BOOL NOT NULL DEFAULT false,
        retention_days INT NOT NULL DEFAULT 90 CHECK (retention_days > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    f"INSERT INTO accounts (id, display_name) VALUES ('{LEGACY}', 'legacy')",
    # provider: keycloak (the login subject) | github | gitlab (a linked code host)
    """CREATE TABLE identities (
        id BIGSERIAL PRIMARY KEY, account_id UUID NOT NULL REFERENCES accounts ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK (provider IN ('keycloak','github','gitlab')),
        subject TEXT NOT NULL, username TEXT,
        linked_at TIMESTAMPTZ NOT NULL DEFAULT now(), unlinked_at TIMESTAMPTZ,
        UNIQUE (provider, subject))""",
    "CREATE INDEX ON identities (account_id)",
    # host_repo_id is the host's immutable id (GitHub repository_id, GitLab project id) so a rename
    # or a deleted-and-recreated repo of the same name cannot inherit another account's registration.
    """CREATE TABLE repos (
        id UUID PRIMARY KEY, account_id UUID NOT NULL REFERENCES accounts ON DELETE CASCADE,
        host TEXT NOT NULL CHECK (host IN ('github','gitlab','other')),
        host_repo_id TEXT, full_name TEXT NOT NULL, clone_url TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','suspended')),
        verified_via TEXT, verify_token TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE UNIQUE INDEX repos_host_id_uq ON repos (host, host_repo_id) WHERE host_repo_id IS NOT NULL",
    "CREATE UNIQUE INDEX repos_clone_url_uq ON repos (clone_url)",
    "CREATE INDEX ON repos (account_id)",
    # API keys are stored as a sha256 hash; only the prefix is kept to tell keys apart in the UI.
    """CREATE TABLE repo_keys (
        id BIGSERIAL PRIMARY KEY, repo_id UUID NOT NULL REFERENCES repos ON DELETE CASCADE,
        key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), revoked_at TIMESTAMPTZ)""",
    """CREATE TABLE llm_keys (
        id BIGSERIAL PRIMARY KEY, account_id UUID NOT NULL REFERENCES accounts ON DELETE CASCADE,
        repo_id UUID REFERENCES repos ON DELETE CASCADE,
        provider TEXT NOT NULL DEFAULT 'nvidia', ciphertext TEXT NOT NULL, hint TEXT,
        validated_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        revoked_at TIMESTAMPTZ)""",
    "CREATE INDEX ON llm_keys (account_id)",
    f"ALTER TABLE runs ADD COLUMN account_id UUID NOT NULL DEFAULT '{LEGACY}' REFERENCES accounts",
    "ALTER TABLE runs ADD COLUMN repo_id UUID REFERENCES repos",
    "CREATE INDEX ON runs (account_id, created_at)",
    # audit_events doubles as the per-account usage log; incidents are per repo+env, not per run
    f"ALTER TABLE audit_events ADD COLUMN account_id UUID NOT NULL DEFAULT '{LEGACY}' REFERENCES accounts",
    f"ALTER TABLE incidents ADD COLUMN account_id UUID NOT NULL DEFAULT '{LEGACY}' REFERENCES accounts",
]


def upgrade() -> None:
    for s in _UP:
        op.execute(s)


def downgrade() -> None:
    for s in ["ALTER TABLE incidents DROP COLUMN IF EXISTS account_id",
              "ALTER TABLE audit_events DROP COLUMN IF EXISTS account_id",
              "ALTER TABLE runs DROP COLUMN IF EXISTS repo_id",
              "ALTER TABLE runs DROP COLUMN IF EXISTS account_id",
              "DROP TABLE IF EXISTS llm_keys", "DROP TABLE IF EXISTS repo_keys",
              "DROP TABLE IF EXISTS repos", "DROP TABLE IF EXISTS identities",
              "DROP TABLE IF EXISTS accounts"]:
        op.execute(s)
