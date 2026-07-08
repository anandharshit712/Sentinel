"""Gateway config (04 §7) — env-driven, host-native. No pydantic-settings; plain os.environ.

Auth is a demo token->role shim (OIDC is Phase 7). Configure with API_TOKENS in .env:
    API_TOKENS="admintok:admin,apprtok:approver,viewtok:viewer"
If API_TOKENS is unset, the Gateway runs OPEN (every request is admin) — dev/demo only.
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
OPEN_MODE = not API_TOKENS  # no tokens configured -> everyone is admin (dev)
