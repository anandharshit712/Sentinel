"""Differential oracle: run a test against both sides of the change (11 §4, O1).

The fix for the hole in Phase 3. Mutation testing asks "if I break the code, does the test
notice?" — it cannot ask "is what the test expects correct?", because the implementation is its
only witness. This module adds a second witness that costs nothing and needs no model: **the other
commit**.

A generated test is run against `base_sha` as well as head. What comes back is more useful than a
correctness verdict:

    passes on BOTH            the test locks behaviour that predates this change. It cannot encode
                              a bug this change introduced — a regression guard.
    passes on HEAD, not base  the test asserts behaviour this change ALTERED. That is the
                              reviewer's actual question, and it is now askable precisely.
    target missing on base    new code; there is no previous behaviour to compare against.

Nothing here is a judgement about whether the change was right — only about what changed. Intent
comes from Tier 2 (11 §3); this tier is pure observation.

Implementation note: a `git worktree` of the base commit, not a checkout, so the run workspace is
never mutated and a concurrent run cannot see a half-switched tree.

    python -m lib.differential        # self-check (needs git)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional

from lib.pyexec import fresh_import_env

logger = logging.getLogger("lib.differential")

PER_RUN_TIMEOUT = 120

# What the comparison concluded. `unknown` is a first-class answer: no base SHA, a repo without
# history, a worktree that would not materialise. Never guessed at.
UNCHANGED = "unchanged_behavior"
CHANGED = "changed_behavior"
NEW_CODE = "new_code"
UNKNOWN = "unknown"


def _git(repo: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def _run_test(cwd: str, test_rel: str, timeout: int = PER_RUN_TIMEOUT) -> tuple[bool, str]:
    """(passed, tail). A timeout counts as not passing — an unbounded test is not evidence."""
    cmd = [sys.executable, "-m", "pytest", test_rel, "-q", "-p", "no:cacheprovider", "-x",
           "--no-header"]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout, check=False,
                           env=fresh_import_env())
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-400:]


def base_has_target(repo: str, base_sha: str, target_file: str, function: str) -> Optional[bool]:
    """Did `function` exist in `target_file` at the base commit? None when it cannot be determined."""
    r = _git(repo, "show", f"{base_sha}:{target_file}")
    if r.returncode != 0:
        return False                      # the file itself is new
    src = r.stdout
    return (f"def {function}" in src) or (f"function {function}" in src)


def compare(repo: str, base_sha: str, target_file: str, function: str, test_path: str,
            test_source: str, head_passed: bool = True) -> Dict[str, Any]:
    """Run `test_source` against the base commit and classify what the comparison shows.

    `repo` is the run workspace at head. It is never modified: the base side runs in a throwaway
    `git worktree`, and the test file is written only inside that worktree.
    """
    evidence: Dict[str, Any] = {"tier": "differential", "base_sha": base_sha,
                                "result": UNKNOWN, "base_passed": None, "head_passed": head_passed}
    if not base_sha:
        evidence["reason"] = "no base commit for this run"
        return evidence

    if _git(repo, "cat-file", "-e", f"{base_sha}^{{commit}}").returncode != 0:
        evidence["reason"] = f"base commit {base_sha[:8]} is not reachable in this workspace"
        return evidence

    existed = base_has_target(repo, base_sha, target_file, function)
    if existed is False:
        evidence.update(result=NEW_CODE,
                        reason=f"{function} does not exist at {base_sha[:8]} — new code, so there "
                               f"is no earlier behaviour to compare against")
        return evidence

    work = tempfile.mkdtemp(prefix="sentinel-diff-")
    tree = os.path.join(work, "base")
    try:
        add = _git(repo, "worktree", "add", "--detach", tree, base_sha, timeout=180)
        if add.returncode != 0:
            evidence["reason"] = f"could not create a base worktree: {add.stderr.strip()[:200]}"
            return evidence

        abs_test = os.path.join(tree, test_path.replace("/", os.sep))
        os.makedirs(os.path.dirname(abs_test), exist_ok=True)
        with open(abs_test, "w", encoding="utf-8") as fh:
            fh.write(test_source)

        base_passed, tail = _run_test(tree, test_path)
        evidence["base_passed"] = base_passed
        if base_passed:
            evidence.update(
                result=UNCHANGED,
                reason="the test passes at the base commit too, so it asserts behaviour that "
                       "predates this change — a regression guard, not a description of the change")
        else:
            evidence.update(
                result=CHANGED, base_output=tail.strip()[-300:],
                reason="the test passes at head and fails at the base commit, so it asserts "
                       "behaviour this change altered. Whether that change was intended is a "
                       "question for the author, not for this tool")
        return evidence
    except Exception as e:                       # never let an oracle failure fail a proposal
        logger.info("differential comparison failed: %s", e)
        evidence["reason"] = f"comparison failed: {e}"
        return evidence
    finally:
        _git(repo, "worktree", "remove", "--force", tree)
        shutil.rmtree(work, ignore_errors=True)


def demo() -> None:
    """Self-check over a real git repo: unchanged behaviour, changed behaviour, and new code."""
    ws = tempfile.mkdtemp(prefix="diff-demo-")

    def w(rel: str, content: str) -> None:
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)

    def sh(*a: str) -> None:
        r = _git(ws, *a)
        assert r.returncode == 0, r.stderr

    subprocess.run(["git", "init", "-q", ws], check=True, capture_output=True)
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")

    w("shop.py", "def price(qty, unit):\n    return qty * unit\n\n\ndef vat(x):\n    return x\n")
    sh("add", "-A")
    sh("commit", "-qm", "base")
    base = _git(ws, "rev-parse", "HEAD").stdout.strip()

    # head changes `price` and adds a brand-new function
    w("shop.py", "def price(qty, unit):\n    return round(qty * unit * 0.9, 2)\n\n\n"
                 "def vat(x):\n    return x\n\n\ndef ship(w):\n    return 5.0\n")
    sh("add", "-A")
    sh("commit", "-qm", "head")

    # 1. a test over behaviour the change ALTERED
    changed = compare(ws, base, "shop.py", "price", "tests/t_price.py",
                      "from shop import price\n\n\ndef test_p():\n    assert price(10, 10) == 90.0\n")
    assert changed["result"] == CHANGED, changed
    assert changed["base_passed"] is False

    # 2. a test over behaviour that PREDATES the change
    unchanged = compare(ws, base, "shop.py", "vat", "tests/t_vat.py",
                        "from shop import vat\n\n\ndef test_v():\n    assert vat(7) == 7\n")
    assert unchanged["result"] == UNCHANGED, unchanged
    assert unchanged["base_passed"] is True

    # 3. a function that did not exist before
    new = compare(ws, base, "shop.py", "ship", "tests/t_ship.py",
                  "from shop import ship\n\n\ndef test_s():\n    assert ship(1) == 5.0\n")
    assert new["result"] == NEW_CODE, new

    # 4. an unusable base is reported, never guessed
    assert compare(ws, "", "shop.py", "price", "t.py", "x")["result"] == UNKNOWN
    assert compare(ws, "0" * 40, "shop.py", "price", "t.py", "x")["result"] == UNKNOWN

    # 5. the run workspace is untouched: no stray test file, no worktree left behind
    assert not os.path.exists(os.path.join(ws, "tests", "t_price.py"))
    assert "base" not in _git(ws, "worktree", "list").stdout

    shutil.rmtree(ws, ignore_errors=True)
    print("lib/differential.py OK")


if __name__ == "__main__":
    demo()
