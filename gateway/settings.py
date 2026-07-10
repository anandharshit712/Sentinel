"""Gateway config (04 §7) — env-driven, host-native. No pydantic-settings; plain os.environ.

Auth is a demo token->role shim (OIDC is Phase 7). Configure with API_TOKENS in .env:
    API_TOKENS="<random>:admin,<random>:approver,<random>:viewer"  # secrets.token_urlsafe(32)
Open mode (every request is admin) is OPT-IN via SENTINEL_OPEN_MODE=1 with no API_TOKENS set;
otherwise, with no tokens configured, privileged routes fail closed (401).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

NEURO_SAN_HOST = os.environ.get("NEURO_SAN_HOST", "localhost")
NEURO_SAN_PORT = int(os.environ.get("NEURO_SAN_PORT", "8080"))
NEURO_SAN_NETWORK = os.environ.get("NEURO_SAN_NETWORK", "sentinel")
INVOKE_TIMEOUT_SECONDS = int(os.environ.get("INVOKE_TIMEOUT_SECONDS", "3700"))

# role ordering for gating: a token's role satisfies any requirement at or below it
ROLE_RANK = {"viewer": 0, "approver": 1, "admin": 2}


def _parse_tokens() -> dict[str, str]:
    raw = os.environ.get("API_TOKENS", "").strip()
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        tok, _, role = pair.partition(":")
        if tok and role in ROLE_RANK:
            out[tok] = role
    return out


API_TOKENS = _parse_tokens()
# C4: open mode (every request is admin) is OPT-IN, never the silent default. With no tokens and
# no explicit opt-in, privileged routes are simply unauthorized (fail closed).
OPEN_MODE = (os.environ.get("SENTINEL_OPEN_MODE", "").lower() in ("1", "true", "yes")
             and not API_TOKENS)

# C3a: repos permitted through simulate. Empty -> allow all (dev). Set SENTINEL_REPO_ALLOWLIST to a
# comma list of repo.name values to restrict which repos may be cloned + executed on the host.
ALLOWED_REPOS = {r.strip() for r in os.environ.get("SENTINEL_REPO_ALLOWLIST", "").split(",") if r.strip()}
