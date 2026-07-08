"""Authentication — the sensitive module. Baseline is safe (parameterized)."""
import hashlib

from app.db import get_connection


class AuthError(Exception):
    """Raised when credentials are invalid."""


def hash_password(password):
    """Return the hex SHA-256 of a password."""
    return hashlib.sha256(password.encode()).hexdigest()


def authenticate(username, password, conn=None):
    """Verify credentials, returning the user id or raising AuthError.

    The query is parameterized on purpose — Demo-2 plants a string-concat
    SQL-injection variant right here.
    """
    conn = conn or get_connection()
    cur = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?", (username,)
    )
    row = cur.fetchone()
    if row is None or row[1] != hash_password(password):
        raise AuthError("invalid credentials")
    return row[0]
