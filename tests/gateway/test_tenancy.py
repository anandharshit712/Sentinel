"""Platform Phase 4: who may do what, and the database refusing cross-account reads by itself.

Unit tests stub Keycloak and the code hosts; the row-level-security tests run against the real dev
database, because "the app filters by account" and "the database refuses" are different claims and
only the second survives a bug in the app.
"""
import datetime as dt
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from db import accounts, dao, models
from gateway import app as gw
from gateway import auth

# ---------------------------------------------------------------- token verification
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Jwks:
    def get_signing_key_from_jwt(self, _tok):
        return type("K", (), {"key": _KEY.public_key()})()


def _sign(claims: dict, key=_KEY) -> str:
    base = {"exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)}
    return jwt.encode({**base, **claims}, key, algorithm="RS256")


@pytest.fixture
def jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: _Jwks())


def test_github_ci_token_yields_the_signed_repository_id(jwks):
    tok = _sign({"iss": auth.GITHUB_ISSUER, "aud": auth.CI_AUDIENCE, "repository_id": 42,
                 "repository": "org/repo"})
    assert auth.verify_ci_token(tok) == {"host": "github", "host_repo_id": "42", "path": "org/repo"}


def test_gitlab_ci_token_yields_the_project_id(jwks):
    tok = _sign({"iss": auth.GITLAB_URL, "aud": auth.CI_AUDIENCE, "project_id": 7,
                 "project_path": "grp/proj"})
    assert auth.verify_ci_token(tok)["host_repo_id"] == "7"


@pytest.mark.parametrize("claims", [
    {"iss": auth.GITHUB_ISSUER, "aud": "someone-else", "repository_id": 1, "repository": "a/b"},
    {"iss": "https://evil.example", "aud": auth.CI_AUDIENCE, "repository_id": 1, "repository": "a/b"},
    {"iss": auth.GITHUB_ISSUER, "aud": auth.CI_AUDIENCE, "repository_id": 1, "repository": "a/b",
     "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)},
])
def test_ci_token_with_wrong_audience_issuer_or_expiry_is_rejected(jwks, claims):
    with pytest.raises(auth.AuthError):
        auth.verify_ci_token(_sign(claims))


def test_ci_token_signed_by_another_key_is_rejected(jwks):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tok = _sign({"iss": auth.GITHUB_ISSUER, "aud": auth.CI_AUDIENCE, "repository_id": 1,
                 "repository": "a/b"}, key=other)
    with pytest.raises(auth.AuthError):
        auth.verify_ci_token(tok)


def test_user_token_minted_for_another_client_is_not_a_session(jwks):
    tok = _sign({"iss": auth.ISSUER, "sub": "u1", "azp": "some-other-client"})
    with pytest.raises(auth.AuthError):
        auth.verify_user_token(tok)
    ok = _sign({"iss": auth.ISSUER, "sub": "u1", "azp": auth.CLIENT_ID})
    assert auth.verify_user_token(ok)["sub"] == "u1"


def test_link_hash_follows_keycloak_formula():
    import base64
    import hashlib
    import urllib.parse
    url = auth.link_url({"sid": "S"}, "github", "http://x/back")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    expect = base64.urlsafe_b64encode(hashlib.sha256(
        (q["nonce"][0] + "S" + auth.CLIENT_ID + "github").encode()).digest()).decode().rstrip("=")
    assert q["hash"][0] == expect


def test_repo_url_normalisation_strips_credentials_and_suffix():
    assert accounts.normalize_url("https://x-access-token:abc@GitHub.com/Org/Repo.git/") == \
        "https://github.com/org/repo"


# ---------------------------------------------------------------- hosted-mode gates
ACCT = {"id": "11111111-1111-1111-1111-111111111111", "email": "a@x", "display_name": "a",
        "status": "active", "is_admin": False, "retention_days": 90}


@pytest.fixture
def oidc(monkeypatch):
    monkeypatch.setattr(dao, "reap_unfinished_runs", lambda: [])
    monkeypatch.setattr(auth, "AUTH_MODE", "oidc")
    monkeypatch.setattr(auth, "verify_user_token", lambda tok: {"sub": "kc-1", "email": "a@x", "sid": "s"})
    state = {"acct": dict(ACCT), "links": {}}
    monkeypatch.setattr(accounts, "account_for_login", lambda *a: state["acct"])
    monkeypatch.setattr(accounts, "linked_hosts", lambda aid: state["links"])
    with TestClient(gw.app) as c:
        c.cookies.set("sentinel_session", "tok")
        yield c, state


def test_nothing_works_until_a_code_host_is_linked(oidc, monkeypatch):
    c, state = oidc
    r = c.get("/api/v1/runs")
    assert r.status_code == 403 and "link_required" in r.text
    monkeypatch.setattr(gw, "_refresh_links", lambda req: {"links": {}, "errors": {}})
    me = c.get("/api/v1/whoami")
    assert me.status_code == 200 and me.json()["link_required"] is True


def test_no_session_is_401(oidc):
    c, _ = oidc
    c.cookies.clear()
    assert c.get("/api/v1/runs").status_code == 401


def test_suspended_account_is_refused_on_every_request(oidc):
    c, state = oidc
    state["acct"]["status"] = "suspended"
    assert c.get("/api/v1/whoami").status_code == 403


def test_account_quota_returns_429(oidc, monkeypatch):
    c, state = oidc
    state["links"] = {"github": {"username": "a"}}
    monkeypatch.setattr(gw, "_registered_repo_for", lambda ev, aid: {"id": "r1"})
    monkeypatch.setattr(accounts, "runs_since", lambda **kw: 10_000)
    ev = {"event_id": "q1", "repo": {"name": "n", "url": "https://github.com/o/r"},
          "change": {"base_sha": "a", "head_sha": "b"},
          "target_transition": {"from_env": "dev", "to_env": "test"}}
    monkeypatch.setattr(dao, "find_run_by_event_id", lambda e: None)
    assert c.post("/api/v1/simulate", json={"event": ev}).status_code == 429


def test_platform_admin_routes_need_the_admin_flag(oidc):
    c, state = oidc
    state["links"] = {"github": {"username": "a"}}
    assert c.get("/api/v1/admin/accounts").status_code == 403


def test_ci_event_must_match_the_credentials_repo(monkeypatch):
    monkeypatch.setattr(dao, "reap_unfinished_runs", lambda: [])
    repo = {"id": "r1", "account_id": ACCT["id"], "status": "active",
            "clone_url": "https://github.com/org/repo"}
    monkeypatch.setattr(accounts, "repo_for_key", lambda k: repo if k == "sk_good" else None)
    monkeypatch.setattr(accounts, "get_account", lambda aid: ACCT)
    ev = {"event_id": "ci-1", "repo": {"name": "x", "url": "https://github.com/org/OTHER"},
          "change": {"base_sha": "a", "head_sha": "b"},
          "target_transition": {"from_env": "dev", "to_env": "test"}}
    with TestClient(gw.app) as c:
        assert c.post("/api/v1/ci/runs", json={"event": ev}, headers={"X-Sentinel-Key": "sk_bad"}).status_code == 401
        r = c.post("/api/v1/ci/runs", json={"event": ev}, headers={"X-Sentinel-Key": "sk_good"})
        assert r.status_code == 403 and "does not match" in r.text


# ---------------------------------------------------------------- the database itself (RLS)
def _db_ok() -> bool:
    try:
        with dao.get_engine().connect() as c:
            return c.execute(text("select to_regclass('sentinel.accounts') is not null")).scalar()
    except Exception:
        return False


db = pytest.mark.skipif(not _db_ok(), reason="dev database with migration 0007 not available")


@pytest.fixture
def two_accounts():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    ra, rb = str(uuid.uuid4()), str(uuid.uuid4())
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        c.execute(models.accounts.insert(), [{"id": a, "display_name": "rls-a"}, {"id": b, "display_name": "rls-b"}])
    ev = {"event_id": "rls", "repo": {"name": "rls-fixture"}, "target_transition": {}}
    with dao.scoped(a):
        dao.insert_run(ra, ev, "manual", "rls-fixture", "dev", "test")
        dao.save_run_payload("review_reports", ra, {"x": 1}, pr_health_score=1, recommendation="approve")
    with dao.scoped(b):
        dao.insert_run(rb, ev, "manual", "rls-fixture", "dev", "test")
    yield a, b, ra, rb
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        for t in ("review_reports", "findings", "audit_events"):
            c.execute(text(f"delete from {t} where run_id in (:x, :y)"), {"x": ra, "y": rb})
        c.execute(text("delete from audit_events where account_id in (:a, :b)"), {"a": a, "b": b})
        c.execute(text("delete from runs where run_id in (:x, :y)"), {"x": ra, "y": rb})
        c.execute(text("delete from accounts where id in (:a, :b)"), {"a": a, "b": b})


@db
def test_the_database_stamps_each_run_with_the_scope_that_created_it(two_accounts):
    a, b, ra, rb = two_accounts
    with dao.scoped(dao.SYSTEM_SCOPE):
        assert dao.get_run(ra)["account_id"] == a and dao.get_run(rb)["account_id"] == b


@db
def test_account_a_cannot_read_bs_run_even_by_direct_id(two_accounts):
    a, b, ra, rb = two_accounts
    with dao.scoped(a):
        assert dao.get_run(ra) is not None
        assert dao.get_run(rb) is None
        assert dao.get_payload("review_reports", ra) is not None
    with dao.scoped(b):
        assert dao.get_payload("review_reports", ra) is None    # child rows follow their run


@db
def test_account_a_cannot_write_into_bs_run(two_accounts):
    a, b, ra, rb = two_accounts
    with dao.scoped(a), pytest.raises(Exception, match="row-level security"):
        dao.save_findings(rb, [{"finding_id": "X", "kind": "security", "severity": "low", "payload": {}}])


@db
def test_an_unscoped_transaction_sees_nothing(two_accounts):
    _, _, ra, _ = two_accounts
    with dao.scoped(""):
        assert dao.get_run(ra) is None
        with dao.get_engine().connect() as c:
            assert c.execute(text("select count(*) from runs")).scalar() == 0


@db
def test_audit_rows_inherit_their_runs_account(two_accounts):
    a, b, ra, rb = two_accounts
    with dao.scoped(dao.SYSTEM_SCOPE):
        dao.record_audit(rb, actor="t", action="rls_probe")
    with dao.scoped(a):
        assert not [e for e in dao.list_audit(rb)]
    with dao.scoped(b):
        assert [e for e in dao.list_audit(rb) if e["action"] == "rls_probe"]


@db
def test_api_key_is_stored_hashed_and_dies_on_revoke(two_accounts):
    a, *_ = two_accounts
    with dao.scoped(a):
        repo = accounts.create_repo(a, host="other", full_name="o/r", clone_url=f"https://example.com/{a}/r",
                                    host_repo_id=None, status="active", verified_via="operator")
        kid, key = accounts.create_repo_key(repo["id"])
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().connect() as c:
        stored = c.execute(text("select key_hash from repo_keys where id = :i"), {"i": kid}).scalar()
    assert key not in stored and len(stored) == 64
    assert accounts.repo_for_key(key)["id"] == repo["id"]
    with dao.scoped(a):
        assert accounts.revoke_repo_key(kid, repo["id"])
    assert accounts.repo_for_key(key) is None
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        c.execute(text("delete from repos where id = :i"), {"i": repo["id"]})


@db
def test_unlinking_a_host_suspends_its_repos_and_revokes_their_keys(two_accounts):
    a, *_ = two_accounts
    accounts.link_identity(a, "github", f"gh-{a}", "someone")
    with dao.scoped(a):
        repo = accounts.create_repo(a, host="github", full_name="o/r", clone_url=f"https://github.com/{a}/r",
                                    host_repo_id=f"id-{a}", status="active", verified_via="admin")
        _, key = accounts.create_repo_key(repo["id"])
        assert accounts.unlink(a, "github") == 1
        assert accounts.get_repo(repo["id"])["status"] == "suspended"
        assert accounts.linked_hosts(a) == {}
    assert accounts.repo_for_key(key) is None
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        c.execute(text("delete from repos where id = :i"), {"i": repo["id"]})
        c.execute(text("delete from identities where account_id = :a"), {"a": a})


@db
def test_a_code_host_account_links_to_only_one_sentinel_account(two_accounts):
    a, b, *_ = two_accounts
    accounts.link_identity(a, "gitlab", f"gl-{a}", "x")
    with pytest.raises(accounts.Conflict):
        accounts.link_identity(b, "gitlab", f"gl-{a}", "x")
    with dao.scoped(dao.SYSTEM_SCOPE), dao.get_engine().begin() as c:
        c.execute(text("delete from identities where account_id = :a"), {"a": a})
