"""Payment processing — authenticates then records a payment."""
from app.auth.login import authenticate


class PaymentError(Exception):
    """Raised when a payment is rejected."""


def process_payment(username, password, amount, conn=None):
    """Authenticate the user and record a captured payment. Returns payment id."""
    from app.db import get_connection

    conn = conn or get_connection()
    user_id = authenticate(username, password, conn=conn)
    if amount <= 0:
        raise PaymentError("amount must be positive")
    cur = conn.execute(
        "INSERT INTO payments (user_id, amount, status) VALUES (?, ?, ?)",
        (user_id, amount, "captured"),
    )
    conn.commit()
    return cur.lastrowid
