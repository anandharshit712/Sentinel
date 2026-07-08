"""Workspace path helpers (07 §1.4) — where a run's shallow clone lives.

The Gateway does the actual `git clone`; coded tools read `repo_workspace` from sly_data.
This module owns only path derivation + cleanup, with a guard against path traversal.
Root = $WORKSPACE_ROOT, else a temp dir (host-native default).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(tempfile.gettempdir()) / "sentinel-workspaces"))

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe(run_id: str) -> str:
    if not run_id or not _SAFE.match(run_id):
        raise ValueError(f"unsafe run_id for workspace path: {run_id!r}")
    return run_id


def workspace_path(run_id: str) -> Path:
    """Absolute path for a run's workspace (does not create it)."""
    return WORKSPACE_ROOT / _safe(run_id)


def ensure_workspace(run_id: str) -> Path:
    """Create (idempotent) and return the run's workspace dir."""
    p = workspace_path(run_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_workspace(run_id: str) -> None:
    """Remove a run's workspace tree if present (best-effort)."""
    shutil.rmtree(workspace_path(run_id), ignore_errors=True)


def demo() -> None:
    rid = "test-0000-1111"
    p = ensure_workspace(rid)
    assert p.is_dir() and p == workspace_path(rid)
    (p / "marker").write_text("x")
    cleanup_workspace(rid)
    assert not p.exists(), "cleanup should remove the tree"
    for bad in ["../etc", "a/b", "", "x;rm"]:
        try:
            workspace_path(bad)
            raise AssertionError(f"expected rejection for {bad!r}")
        except ValueError:
            pass
    print(f"workspace OK: root={WORKSPACE_ROOT}, create/cleanup + traversal guard verified")


if __name__ == "__main__":
    demo()
