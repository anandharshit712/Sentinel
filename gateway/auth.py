"""Hosted-mode identity (12 §5): Keycloak sign-in, code-host linking, repo ownership, CI tokens.

Sentinel writes no login security of its own. Keycloak owns passwords, email verification,
brute-force lockout, 2FA and the GitHub/GitLab sign-in; this module only verifies the tokens it
issues and asks the code hosts what a linked user may do.

    AUTH_MODE=oidc
    KEYCLOAK_URL=http://localhost:8180          # as the BROWSER sees it (token `iss`)
    KEYCLOAK_INTERNAL_URL=http://keycloak:8080  # as the Gateway reaches it (defaults to KEYCLOAK_URL)
    KEYCLOAK_REALM=sentinel
    KEYCLOAK_CLIENT_ID=sentinel-gateway
    KEYCLOAK_CLIENT_SECRET=...
    PUBLIC_URL=http://localhost:8000            # where the SPA/Gateway is served
    CI_OIDC_AUDIENCE=sentinel                   # `aud` CI job tokens must carry
    GITLAB_URL=https://gitlab.com
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

import jwt

AUTH_MODE = os.environ.get("AUTH_MODE", "tokens").strip().lower()
KC_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8180").rstrip("/")
KC_INTERNAL = os.environ.get("KEYCLOAK_INTERNAL_URL", KC_URL).rstrip("/")
REALM = os.environ.get("KEYCLOAK_REALM", "sentinel")
CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "sentinel-gateway")
CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
CI_AUDIENCE = os.environ.get("CI_OIDC_AUDIENCE", "sentinel")
GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")

ISSUER = f"{KC_URL}/realms/{REALM}"
_OIDC = f"{KC_INTERNAL}/realms/{REALM}/protocol/openid-connect"
CALLBACK = f"{PUBLIC_URL}/auth/callback"
HOSTS = ("github", "gitlab")

# CI identity providers: issuer -> how to read the repository out of the job token
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"


class AuthError(Exception):
    pass


def _http(url: str, *, data: dict | None = None, headers: dict | None = None,
          method: str | None = None, timeout: float = 10) -> tuple[int, bytes]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https/http config URLs
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------------------------------------------------------------- Keycloak sign-in (BFF)
def login_url(state: str) -> str:
    q = {"client_id": CLIENT_ID, "response_type": "code", "scope": "openid email profile",
         "redirect_uri": CALLBACK, "state": state}
    return f"{ISSUER}/protocol/openid-connect/auth?{urllib.parse.urlencode(q)}"


def logout_url() -> str:
    return (f"{ISSUER}/protocol/openid-connect/logout?"
            + urllib.parse.urlencode({"client_id": CLIENT_ID, "post_logout_redirect_uri": PUBLIC_URL}))


def exchange_code(code: str) -> dict:
    status, body = _http(f"{_OIDC}/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    if status != 200:
        raise AuthError(f"code exchange failed ({status})")
    return json.loads(body)


def refresh(refresh_token: str) -> dict:
    status, body = _http(f"{_OIDC}/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    if status != 200:
        raise AuthError(f"session refresh failed ({status})")
    return json.loads(body)


@lru_cache(maxsize=4)
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_user_token(token: str) -> dict:
    """Claims of a Keycloak access token issued to THIS client, or AuthError."""
    try:
        key = _jwks(f"{_OIDC}/certs").get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER,
                            options={"verify_aud": False, "require": ["exp", "sub", "iss"]})
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid session: {e}") from e
    if claims.get("azp") != CLIENT_ID:     # a token minted for another client is not a session here
        raise AuthError("token was not issued to the Sentinel client")
    return claims


# ---------------------------------------------------------------- code-host linking
def broker_token(user_token: str, provider: str) -> str | None:
    """The code-host OAuth token Keycloak stored for this user, or None when not linked.
    Needs the IdP's `storeToken` and the user's broker/read-token role (realm default role)."""
    status, body = _http(f"{KC_INTERNAL}/realms/{REALM}/broker/{provider}/token",
                         headers={"Authorization": f"Bearer {user_token}"})
    if status != 200:
        return None
    text = body.decode()
    try:   # GitLab stores JSON; GitHub stores a form-encoded string
        return json.loads(text).get("access_token")
    except ValueError:
        return (urllib.parse.parse_qs(text).get("access_token") or [None])[0]


def link_url(claims: dict, provider: str, redirect_uri: str) -> str:
    """Keycloak client-initiated account linking (Server Admin guide, 'Client initiated account
    linking'): hash = b64url(sha256(nonce + session_state + client_id + provider))."""
    nonce = secrets.token_urlsafe(16)
    sid = claims.get("sid") or claims.get("session_state") or ""
    digest = hashlib.sha256((nonce + sid + CLIENT_ID + provider).encode()).digest()
    h = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    q = {"client_id": CLIENT_ID, "redirect_uri": redirect_uri, "nonce": nonce, "hash": h}
    return f"{ISSUER}/broker/{provider}/link?{urllib.parse.urlencode(q)}"


def _service_token() -> str:
    status, body = _http(f"{_OIDC}/token", data={"grant_type": "client_credentials",
                                                  "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    if status != 200:
        raise AuthError(f"service-account token failed ({status})")
    return json.loads(body)["access_token"]


def remove_federated_identity(user_sub: str, provider: str) -> None:
    """Unlink in Keycloak too, so the stored host token is gone, not just ignored."""
    status, _ = _http(f"{KC_INTERNAL}/admin/realms/{REALM}/users/{user_sub}/federated-identity/{provider}",
                      headers={"Authorization": f"Bearer {_service_token()}"}, method="DELETE")
    if status not in (204, 404):
        raise AuthError(f"Keycloak unlink failed ({status})")


def _host_get(provider: str, path: str, token: str) -> tuple[int, dict]:
    base = "https://api.github.com" if provider == "github" else f"{GITLAB_URL}/api/v4"
    hdr = {"Accept": "application/json", "User-Agent": "sentinel-gateway"}
    if token:   # empty = anonymous lookup of a public repo (token mode registration)
        hdr["Authorization"] = f"Bearer {token}"
    status, body = _http(base + path, headers=hdr)
    try:
        return status, json.loads(body or b"{}")
    except ValueError:
        return status, {}


def host_user(provider: str, token: str) -> tuple[str, str]:
    """(immutable user id, username) of the linked code-host account."""
    status, u = _host_get(provider, "/user", token)
    if status != 200:
        raise AuthError(f"{provider} rejected the linked token ({status})")
    return str(u["id"]), u.get("login") or u.get("username") or ""


def repo_ownership(provider: str, token: str, full_name: str) -> dict:
    """Ask the host whether the linked user administers this repo. Registration requires GitHub
    `permissions.admin` or GitLab access level >= Maintainer (40) — a contributor cannot register
    someone else's repo and point Sentinel at it."""
    if provider == "github":
        status, r = _host_get("github", f"/repos/{full_name}", token)
        if status != 200:
            raise AuthError(f"GitHub cannot see {full_name} with your linked account ({status})")
        return {"host_repo_id": str(r["id"]), "full_name": r["full_name"], "clone_url": r["clone_url"],
                "owner": bool((r.get("permissions") or {}).get("admin")), "via": "admin"}
    status, r = _host_get("gitlab", f"/projects/{urllib.parse.quote(full_name, safe='')}", token)
    if status != 200:
        raise AuthError(f"GitLab cannot see {full_name} with your linked account ({status})")
    perms = r.get("permissions") or {}
    level = max((perms.get("project_access") or {}).get("access_level") or 0,
                (perms.get("group_access") or {}).get("access_level") or 0)
    return {"host_repo_id": str(r["id"]), "full_name": r["path_with_namespace"],
            "clone_url": r["http_url_to_repo"], "owner": level >= 40, "via": "maintainer"}


# ---------------------------------------------------------------- CI job tokens (OIDC)
def verify_ci_token(token: str) -> dict:
    """A GitHub Actions or GitLab CI OIDC job token -> {host, host_repo_id, path}.

    No long-lived secret is involved: the job asks its CI platform for a short-lived JWT with our
    audience, and the repository id inside it is signed by the platform. The id is matched against
    the registered repo's immutable host id, never its (renamable) name."""
    try:
        iss = jwt.decode(token, options={"verify_signature": False}).get("iss", "")
    except jwt.PyJWTError as e:
        raise AuthError(f"malformed CI token: {e}") from e
    if iss == GITHUB_ISSUER:
        host, certs = "github", f"{GITHUB_ISSUER}/.well-known/jwks"
    elif iss == GITLAB_URL:
        host, certs = "gitlab", f"{GITLAB_URL}/oauth/discovery/keys"
    else:
        raise AuthError(f"untrusted CI token issuer {iss!r}")
    try:
        key = _jwks(certs).get_signing_key_from_jwt(token).key
        c = jwt.decode(token, key, algorithms=["RS256"], issuer=iss, audience=CI_AUDIENCE)
    except jwt.PyJWTError as e:
        raise AuthError(f"CI token rejected: {e}") from e
    if host == "github":
        return {"host": host, "host_repo_id": str(c["repository_id"]), "path": c["repository"]}
    return {"host": host, "host_repo_id": str(c["project_id"]), "path": c["project_path"]}
