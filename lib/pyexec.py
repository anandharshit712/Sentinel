"""Subprocess environment for running code we just rewrote (11).

Python caches compiled bytecode in `__pycache__` and decides whether the cache is stale from the
source file's **mtime and size**. Rewrite a file with a same-length edit inside the same second —
`0.90` to `0.85`, `10` to `11` — and the interpreter reuses the old bytecode and runs the code you
just replaced.

That is exactly what mutation testing does: write a one-character mutant over the target, run the
test, repeat, many times per second. A size-preserving mutant could silently never take effect, the
test would pass, and the mutant would be recorded as **missed** — deflating the score that the
whole evaluation rests on. Found by a demo whose "corrected" implementation kept returning the
buggy value.

`PYTHONPYCACHEPREFIX` sends any cache to a fresh directory per call, so there is never a stale entry
to find, and `PYTHONDONTWRITEBYTECODE` stops us littering the repository under test.

    python -m lib.pyexec        # self-check (proves the staleness and the fix)
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional


def fresh_import_env(base: Optional[Dict[str, str]] = None,
                     repo: Optional[str] = None) -> Dict[str, str]:
    """A subprocess env that cannot read stale bytecode from a previous write.

    With `repo`, a `src/` layout inside it is put first on PYTHONPATH. Without that, a src-layout
    package is importable only through an installed copy — which points at the ORIGINAL tree, so a
    mutant written into a sandbox copy would never run and every mutant would read as "missed".
    """
    env = dict(base if base is not None else os.environ)
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="sentinel-pyc-")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if repo and os.path.isdir(os.path.join(repo, "src")):
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (os.path.join(repo, "src"), env.get("PYTHONPATH", "")) if p)
    return env


def demo() -> None:
    import shutil
    import subprocess
    import sys

    ws = tempfile.mkdtemp(prefix="pyexec-demo-")
    mod = os.path.join(ws, "m.py")

    def run(env=None) -> str:
        r = subprocess.run([sys.executable, "-c", "import m; print(m.f())"], cwd=ws, env=env,
                           capture_output=True, text=True, check=False)
        return r.stdout.strip()

    # a same-size edit written in the same second as the first import
    with open(mod, "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 90.0\n")
    run()                                     # writes __pycache__/m.*.pyc
    with open(mod, "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 85.0\n")

    stale = run()
    fresh = run(fresh_import_env())
    assert fresh == "85.0", f"the fix must always see the current source, got {fresh!r}"
    # The bug itself is timing-dependent (it needs both writes inside one mtime granule), so it is
    # reported rather than asserted — the guarantee under test is that the fix is correct either way.
    # src layout: the package under src/ must resolve to THIS tree
    os.makedirs(os.path.join(ws, "src", "pkg"))
    with open(os.path.join(ws, "src", "pkg", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("V = 'sandbox'\n")
    r = subprocess.run([sys.executable, "-c", "import pkg; print(pkg.V)"], cwd=ws,
                       env=fresh_import_env(repo=ws), capture_output=True, text=True, check=False)
    assert r.stdout.strip() == "sandbox", r.stderr

    print(f"lib/pyexec.py OK (default env read {stale!r}, fresh env read {fresh!r})")
    shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    demo()
