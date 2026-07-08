"""SQLite access for the payments service."""
import sqlite3

_conn = None


def init_schema(conn):
    """Create the users and payments tables if absent."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS payments "
        "(id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT)"
    )
    conn.commit()


def get_connection():
    """Return the shared connection, seeding a demo user on first use."""
    # ponytail: single shared in-memory conn — fine for a demo fixture, not production.
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_schema(_conn)
        from app.auth.login import hash_password
        _conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("alice", hash_password("s3cret")),
        )
        _conn.commit()
    return _conn
