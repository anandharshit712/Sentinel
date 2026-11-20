"""Tenancy DAO (12 §5): accounts, linked code hosts, registered repos, CI keys, BYOK keys, quotas.

Lookups that must happen BEFORE the caller's account is known (a login, a CI token, an API key)
run in the system scope explicitly; everything else runs in whatever scope the request set, so
row-level security still fences it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import secrets
import uuid

from sqlalchemy import func, select

from db import dao, models

_now = lambda: _dt.datetime.now(_dt.timezone.utc)  # noqa: E731


class Conflict(Exception):
    """The thing already belongs to another account (a code-host identity, a repo)."""


def _admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("SENTINEL_ADMIN_EMAILS", "").split(",") if e.strip()}


# ---------------------------------------------------------------- accounts / identities
def account_for_login(sub: str, email: str | None, name: str | None) -> dict:
    """The account behind a Keycloak subject, created on first sight. System scope: the caller has
    no account yet."""
    a, i = models.accounts, models.identities
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        row = c.execute(select(a).join(i, i.c.account_id == a.c.id)
                        .where(i.c.provider == "keycloak", i.c.subject == sub)).mappings().first()
        if row:
            return dict(row)
        aid = str(uuid.uuid4())
        c.execute(a.insert().values(id=aid, email=email, display_name=name or email,
                                    is_admin=(email or "").lower() in _admin_emails()))
        c.execute(i.insert().values(account_id=aid, provider="keycloak", subject=sub, username=email))
        return dict(c.execute(select(a).where(a.c.id == aid)).mappings().one())


def get_account(account_id: str) -> dict | None:
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        row = c.execute(select(models.accounts).where(models.accounts.c.id == account_id)).mappings().first()
    return dict(row) if row else None


def linked_hosts(account_id: str) -> dict[str, dict]:
    """provider -> identity row, for code hosts currently linked to this account."""
    i = models.identities
    with dao.get_engine().connect() as c:
        rows = c.execute(select(i).where(i.c.account_id == account_id, i.c.provider != "keycloak",
                                         i.c.unlinked_at.is_(None))).mappings().all()
    return {r["provider"]: dict(r) for r in rows}


def link_identity(account_id: str, provider: str, subject: str, username: str | None) -> None:
    """One code-host account belongs to at most one Sentinel account (UNIQUE provider, subject)."""
    i = models.identities
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        row = c.execute(select(i).where(i.c.provider == provider, i.c.subject == subject)).mappings().first()
        if row and row["account_id"] != account_id:
            raise Conflict(f"this {provider} account is already linked to another Sentinel account")
        if row:
            c.execute(i.update().where(i.c.id == row["id"]).values(unlinked_at=None, username=username))
        else:
            c.execute(i.insert().values(account_id=account_id, provider=provider, subject=subject,
                                        username=username))


def unlink(account_id: str, provider: str) -> int:
    """Unlink a code host and, in the same transaction, suspend every repo registered through it and
    revoke their CI keys — access derived from the link ends with the link. Returns repos suspended."""
    i, r, k = models.identities, models.repos, models.repo_keys
    with dao.scoped(account_id), dao.get_engine().begin() as c:
        c.execute(i.update().where(i.c.account_id == account_id, i.c.provider == provider,
                                   i.c.unlinked_at.is_(None)).values(unlinked_at=_now()))
        ids = [x[0] for x in c.execute(r.update().where(r.c.account_id == account_id, r.c.host == provider,
                                                        r.c.status != "suspended")
                                       .values(status="suspended").returning(r.c.id))]
        if ids:
            c.execute(k.update().where(k.c.repo_id.in_(ids), k.c.revoked_at.is_(None)).values(revoked_at=_now()))
    return len(ids)


def list_accounts() -> list[dict]:
    a = models.accounts
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        return [dict(m) for m in c.execute(select(a).order_by(a.c.created_at)).mappings()]


def set_account_status(account_id: str, status: str) -> bool:
    a = models.accounts
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        return c.execute(a.update().where(a.c.id == account_id).values(status=status)).rowcount > 0


# ---------------------------------------------------------------- repos
def normalize_url(url: str) -> str:
    """https://user:tok@GitHub.com/Org/Repo.git -> https://github.com/org/repo (identity of a repo
    for matching an event's clone URL against a registration)."""
    from urllib.parse import urlsplit
    p = urlsplit(url.strip())
    path = p.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"https://{(p.hostname or '').lower()}{path.lower()}"


def create_repo(account_id: str, *, host: str, full_name: str, clone_url: str, host_repo_id: str | None,
                status: str, verified_via: str | None, verify_token: str | None = None) -> dict:
    r = models.repos
    url = normalize_url(clone_url)
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        clash = c.execute(select(r.c.account_id).where(
            (r.c.clone_url == url) | ((r.c.host == host) & (r.c.host_repo_id == host_repo_id)
                                      if host_repo_id else False))).first()
        if clash and clash[0] != account_id:
            raise Conflict("this repository is registered to another Sentinel account")
        if clash:
            raise Conflict("this repository is already registered")
        rid = str(uuid.uuid4())
        c.execute(r.insert().values(id=rid, account_id=account_id, host=host, host_repo_id=host_repo_id,
                                    full_name=full_name, clone_url=url, status=status,
                                    verified_via=verified_via, verify_token=verify_token))
        return dict(c.execute(select(r).where(r.c.id == rid)).mappings().one())


def list_repos(account_id: str) -> list[dict]:
    r = models.repos
    with dao.get_engine().connect() as c:
        return [dict(m) for m in c.execute(select(r).where(r.c.account_id == account_id)
                                           .order_by(r.c.created_at)).mappings()]


def get_repo(repo_id: str) -> dict | None:
    """Runs in the caller's scope: another account's repo id simply does not exist here."""
    with dao.get_engine().connect() as c:
        row = c.execute(select(models.repos).where(models.repos.c.id == repo_id)).mappings().first()
    return dict(row) if row else None


def activate_repo(repo_id: str, via: str) -> None:
    r = models.repos
    with dao.get_engine().begin() as c:
        c.execute(r.update().where(r.c.id == repo_id).values(status="active", verified_via=via,
                                                             verify_token=None))


def find_repo_by_host_id(host: str, host_repo_id: str) -> dict | None:
    r = models.repos
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        row = c.execute(select(r).where(r.c.host == host, r.c.host_repo_id == str(host_repo_id))).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------- CI API keys
def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_repo_key(repo_id: str) -> tuple[int, str]:
    """Returns (id, plaintext). The plaintext exists only in this return value."""
    key = "sk_" + secrets.token_urlsafe(32)
    with dao.get_engine().begin() as c:
        row = c.execute(models.repo_keys.insert().returning(models.repo_keys.c.id),
                        {"repo_id": repo_id, "key_hash": _hash(key), "prefix": key[:10]}).first()
    return int(row[0]), key


def list_repo_keys(repo_id: str) -> list[dict]:
    k = models.repo_keys
    with dao.get_engine().connect() as c:
        return [dict(m) for m in c.execute(select(k.c.id, k.c.prefix, k.c.created_at, k.c.revoked_at)
                                           .where(k.c.repo_id == repo_id)).mappings()]


def revoke_repo_key(key_id: int, repo_id: str) -> bool:
    k = models.repo_keys
    with dao.get_engine().begin() as c:
        return c.execute(k.update().where(k.c.id == key_id, k.c.repo_id == repo_id,
                                          k.c.revoked_at.is_(None))
                         .values(revoked_at=_now())).rowcount > 0


def repo_for_key(key: str) -> dict | None:
    """Active repo for a live (unrevoked) API key. System scope: the key IS the credential."""
    k, r = models.repo_keys, models.repos
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        row = c.execute(select(r).join(k, k.c.repo_id == r.c.id)
                        .where(k.c.key_hash == _hash(key), k.c.revoked_at.is_(None))).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------- BYOK
def save_llm_key(account_id: str, repo_id: str | None, ciphertext: str, hint: str) -> int:
    """A new key revokes the previous one for the same (account, repo) slot."""
    k = models.llm_keys
    with dao.get_engine().begin() as c:
        slot = (k.c.account_id == account_id) & (k.c.repo_id == repo_id if repo_id else k.c.repo_id.is_(None))
        c.execute(k.update().where(slot, k.c.revoked_at.is_(None)).values(revoked_at=_now()))
        row = c.execute(k.insert().returning(k.c.id),
                        {"account_id": account_id, "repo_id": repo_id, "ciphertext": ciphertext,
                         "hint": hint, "validated_at": _now()}).first()
    return int(row[0])


def llm_key_for(account_id: str, repo_id: str | None) -> str | None:
    """Ciphertext of the key a run should use: the repo's own key, else the account-wide one."""
    k = models.llm_keys
    with dao.scoped(account_id), dao.get_engine().connect() as c:
        rows = c.execute(select(k.c.repo_id, k.c.ciphertext).where(
            k.c.account_id == account_id, k.c.revoked_at.is_(None))).all()
    by_repo = {r[0]: r[1] for r in rows}
    return by_repo.get(repo_id) if repo_id and repo_id in by_repo else by_repo.get(None)


def revoke_llm_key(account_id: str, key_id: int) -> bool:
    k = models.llm_keys
    with dao.get_engine().begin() as c:
        return c.execute(k.update().where(k.c.id == key_id, k.c.account_id == account_id,
                                          k.c.revoked_at.is_(None)).values(revoked_at=_now())).rowcount > 0


def list_llm_keys(account_id: str) -> list[dict]:
    k = models.llm_keys
    with dao.get_engine().connect() as c:
        return [dict(m) for m in c.execute(
            select(k.c.id, k.c.repo_id, k.c.provider, k.c.hint, k.c.validated_at, k.c.created_at)
            .where(k.c.account_id == account_id, k.c.revoked_at.is_(None))).mappings()]


# ---------------------------------------------------------------- quotas / retention
def runs_since(*, account_id: str | None = None, repo_id: str | None = None, hours: int = 24) -> int:
    r = models.runs
    q = select(func.count()).select_from(r).where(r.c.created_at > _now() - _dt.timedelta(hours=hours))
    if account_id:
        q = q.where(r.c.account_id == account_id)
    if repo_id:
        q = q.where(r.c.repo_id == repo_id)
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        return int(c.execute(q).scalar() or 0)


# children first: the per-run foreign keys have no ON DELETE CASCADE (04 §8)
_PER_RUN = ["findings", "review_reports", "review_plans", "coverage_gaps", "test_proposals", "test_plans",
            "test_results", "env_contexts", "risk_scores", "decisions", "approvals", "outcomes",
            "notifications", "audit_events"]


def purge_expired() -> dict[str, int]:
    """Delete each account's runs older than its retention_days, with everything hanging off them.
    System work (called by the Gateway's daily loop). Returns account_id -> runs purged."""
    out: dict[str, int] = {}
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        for acc in c.execute(select(models.accounts.c.id, models.accounts.c.retention_days)).all():
            cutoff = _now() - _dt.timedelta(days=int(acc[1]))
            ids = [x[0] for x in c.execute(select(models.runs.c.run_id).where(
                models.runs.c.account_id == acc[0], models.runs.c.created_at < cutoff))]
            if not ids:
                continue
            for t in _PER_RUN:
                tbl = models.metadata.tables[f"sentinel.{t}"]
                c.execute(tbl.delete().where(tbl.c.run_id.in_(ids)))
            c.execute(models.runs.delete().where(models.runs.c.run_id.in_(ids)))
            out[str(acc[0])] = len(ids)
    return out
