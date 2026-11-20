"""Encrypt stored secrets (BYOK LLM keys) with one key-encryption key from the environment.

    SENTINEL_SECRET_KEY=<Fernet key>      # python -m lib.secretbox --new-key

Fernet (AES-128-CBC + HMAC-SHA256) from `cryptography`, which pyjwt[crypto] already brings in.
The database only ever holds ciphertext plus a 4-character hint; losing SENTINEL_SECRET_KEY means
every stored key must be re-entered, which is the intended failure mode.
"""
from __future__ import annotations

import os
import sys

from cryptography.fernet import Fernet, InvalidToken


def _box() -> Fernet:
    key = os.environ.get("SENTINEL_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("SENTINEL_SECRET_KEY is not set; cannot store or read LLM keys")
    return Fernet(key.encode())


def seal(plaintext: str) -> str:
    return _box().encrypt(plaintext.encode()).decode()


def open_(ciphertext: str) -> str:
    try:
        return _box().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("stored key cannot be decrypted with the current SENTINEL_SECRET_KEY") from e


def hint(plaintext: str) -> str:
    return "…" + plaintext.strip()[-4:]


def demo() -> None:
    os.environ["SENTINEL_SECRET_KEY"] = Fernet.generate_key().decode()
    c = seal("nvapi-secret-1234")
    assert "secret" not in c and open_(c) == "nvapi-secret-1234"
    assert hint("nvapi-secret-1234") == "…1234"
    os.environ["SENTINEL_SECRET_KEY"] = Fernet.generate_key().decode()
    try:
        open_(c)
        raise AssertionError("decrypted with the wrong key")
    except RuntimeError:
        pass
    print("lib/secretbox.py OK")


if __name__ == "__main__":
    if "--new-key" in sys.argv:
        print(Fernet.generate_key().decode())
    else:
        demo()
