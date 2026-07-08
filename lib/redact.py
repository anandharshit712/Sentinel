"""Log redaction (04 §12, HLD §7) — scrub secrets before they reach any log sink.

Applied as a logging.Filter on both the neuro-san and Gateway log pipelines. Redaction
is a trust-boundary control: keep it conservative (over-redact rather than leak).
"""
from __future__ import annotations

import logging
import re

# (compiled pattern, replacement). Order matters: specific tokens before generic key=val.
_RULES: list[tuple[re.Pattern, str]] = [
    # credentials embedded in a URI:  scheme://user:PASSWORD@host  -> redact the password
    (re.compile(r"([a-zA-Z][\w+.\-]*://[^:@\s/]+:)([^@\s/]+)(@)"), r"\1<redacted>\3"),
    # AWS access key id
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted:aws-key>"),
    # PEM private key block
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     "<redacted:pem>"),
    # JWT (three base64url segments)
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "<redacted:jwt>"),
    # generic  key = value  /  key: value  for secret-ish keys
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|nvidia_api_key)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;]+"),
     r"\1=<redacted>"),
]


def redact(text: str) -> str:
    """Return `text` with known secret shapes replaced by redaction markers."""
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


class RedactionFilter(logging.Filter):
    """logging.Filter that redacts the fully-formatted message of each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
        except Exception:  # never let redaction break logging
            record.msg = redact(str(record.msg))
        record.args = None
        return True


def demo() -> None:
    vectors = [
        ("postgresql+psycopg://sentinel:s3cr3t@localhost:5432/sentinel", "s3cr3t"),
        ("aws key AKIAABCDEFGHIJKLMNOP here", "AKIAABCDEFGHIJKLMNOP"),
        ("Authorization: eyJhbGciOi.JzdWIiOiIx.SflKxwRJSME", "eyJhbGciOi"),
        ("NVIDIA_API_KEY=nvapi-abc123def", "nvapi-abc123def"),
        ('config {"password": "hunter2"}', "hunter2"),
    ]
    for raw, secret in vectors:
        out = redact(raw)
        assert secret not in out, f"leak: {secret!r} survived in {out!r}"
    benign = "run 3f2a completed in 42ms with 12 tests passed"
    assert redact(benign) == benign, "benign text must be untouched"
    print(f"redact OK: {len(vectors)} secret vectors scrubbed, benign untouched")


if __name__ == "__main__":
    demo()
