import sqlite3

import pytest

from app.auth.login import AuthError, authenticate, hash_password
from app.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("bob", hash_password("pw")),
    )
    c.commit()
    return c


def test_authenticate_ok(conn):
    assert authenticate("bob", "pw", conn=conn) == 1


def test_authenticate_bad_password(conn):
    with pytest.raises(AuthError):
        authenticate("bob", "wrong", conn=conn)


def test_authenticate_unknown_user(conn):
    with pytest.raises(AuthError):
        authenticate("nobody", "pw", conn=conn)
