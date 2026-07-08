import sqlite3

import pytest

from app.auth.login import hash_password
from app.db import init_schema
from app.payments.processor import PaymentError, process_payment


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


def test_process_payment_ok(conn):
    pid = process_payment("bob", "pw", 42.0, conn=conn)
    assert pid == 1
    row = conn.execute("SELECT amount, status FROM payments WHERE id = ?", (pid,)).fetchone()
    assert row == (42.0, "captured")


def test_process_payment_rejects_nonpositive(conn):
    with pytest.raises(PaymentError):
        process_payment("bob", "pw", 0, conn=conn)
