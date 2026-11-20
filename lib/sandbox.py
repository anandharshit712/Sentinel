"""Per-run sandbox: the one place repository code is executed (platform plan, Phase 2).

Every call that runs code from the repository under review (installs, tests, mutation runs,
oracle probes) goes through `run()`. Read-only git and tree-sitter parsing stay in-process: they
never execute repository code.

    SANDBOX_MODE=local   (default) plain subprocess on this machine — host-native dev and unit tests.
    SANDBOX_MODE=docker  sandboxd (sandboxd/server.py) runs the call in the run's own container:
                         only that run's workspace mounted, no host environment, network off unless
                         the caller asks for it (install phase), CPU/mem/pids capped, removed at
                         close_run.

Callers pass host paths. sandboxd maps the workspace to /work and the run's scratch dir
(`scratch_dir()`, a sibling of the workspace) to /scratch, so anything a sandboxed process must
read or write has to live in one of those two places.

    python -m lib.sandbox        # self-check (local mode)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API|DATABASE_URL|NVIDIA|CRED)", re.I)
SCRATCH_SUFFIX = ".sentinel-tmp"
WORK = "/work"           # where sandboxd mounts the workspace inside the container
_REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "repo_config.yaml"


def mode() -> str:
    return os.environ.get("SANDBOX_MODE", "local").strip().lower() or "local"


def scrub(env: Mapping[str, str]) -> Dict[str, str]:
    """Drop anything that looks like a credential (LLM key, DB URL, tokens)."""
    return {k: v for k, v in env.items() if not SECRET_ENV.search(k)}


def which(name: str) -> Optional[str]:
    """A tool on the PATH of wherever the code will run: the host, or the sandbox image."""
    return name if mode() == "docker" else shutil.which(name)


def scratch_dir(workspace: str) -> Optional[str]:
    """Directory for temp files a sandboxed process must see. None = the system temp dir.

    In docker mode it is `<workspace>.sentinel-tmp` — beside the workspace, not inside it, so the
    repository tree stays untouched and no ancestor of a copied repo carries its config.
    """
    if mode() != "docker":
        return None
    d = workspace.rstrip("\\/") + SCRATCH_SUFFIX
    os.makedirs(d, exist_ok=True)
    return d


def run(run_id: Optional[str], argv: List[str], cwd: str, timeout: float, network: bool = False,
        env: Optional[Mapping[str, str]] = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run repository code. Same contract as `subprocess.run(..., capture_output=True, text)`:
    raises TimeoutExpired on timeout, CalledProcessError when `check` and the exit is non-zero.

    `env` is scrubbed of secrets in both modes. `network` only takes effect in docker mode.
    """
    base = scrub(os.environ if env is None else env)
    argv = [str(a) for a in argv]
    if mode() != "docker":
        r = subprocess.run(argv, cwd=cwd, env=base, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout, check=False,
                           stdin=subprocess.DEVNULL)
    else:
        if not run_id:
            raise RuntimeError("sandbox: docker mode needs a run_id — refusing to run repository "
                               "code outside a run's container")
        # Only what the caller changed travels; the host's own environment never does.
        extra = {k: v for k, v in base.items() if os.environ.get(k) != v}
        out = _call("/exec", {"run_id": str(run_id), "argv": argv, "cwd": str(cwd),
                              "timeout": int(timeout), "network": bool(network), "env": extra},
                    timeout=timeout + 60)
        if out.get("timed_out"):
            raise subprocess.TimeoutExpired(argv, timeout, output=out.get("stdout"),
                                            stderr=out.get("stderr"))
        r = subprocess.CompletedProcess(argv, int(out["returncode"]), out.get("stdout", ""),
                                        out.get("stderr", ""))
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, argv, r.stdout, r.stderr)
    return r


def open_run(run_id: str, workspace_dir: str, repo_name: Optional[str] = None) -> None:
    """Create the run's container (docker mode; no-op locally). Call once the workspace exists,
    before the agent network starts. `repo_name` selects the runner_quota and the per-repo
    dependency cache; without it the run gets the default quota and no cache."""
    if mode() != "docker":
        return
    q = _quota(repo_name)
    _call("/create", {
        "run_id": str(run_id), "workspace": str(workspace_dir),
        "cache_key": hashlib.sha256(repo_name.encode()).hexdigest()[:16] if repo_name else None,
        "cpus": str(q.get("cpu", "2")),
        "memory": str(q.get("mem", "2Gi")).replace("Gi", "g").replace("Mi", "m"),
    }, timeout=180)


def close_run(run_id: str) -> None:
    """Remove the run's container and scratch dir (docker mode; no-op locally). Safe to call twice."""
    if mode() != "docker":
        return
    _call("/destroy", {"run_id": str(run_id)}, timeout=120)


def _quota(repo_name: Optional[str]) -> Dict[str, Any]:
    try:
        with open(_REPO_CONFIG, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except OSError:
        return {}
    repo = ((cfg.get("repos") or {}).get(repo_name) or {}) if repo_name else {}
    return repo.get("runner_quota") or (cfg.get("defaults") or {}).get("runner_quota") or {}


def _call(path: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    url = os.environ.get("SANDBOXD_URL", "http://127.0.0.1:8090").rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get("SANDBOXD_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"sandboxd {path}: HTTP {e.code} {e.read()[:300].decode('utf-8', 'replace')}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"sandboxd {path} unreachable at {url}: {e}")


def demo() -> None:
    import sys
    import tempfile
    assert mode() == "local", "self-check runs in local mode"
    ws = tempfile.mkdtemp(prefix="sandbox-demo-")
    os.environ["SANDBOX_DEMO_API_KEY"] = "leak-me"
    try:
        r = run(None, [sys.executable, "-c",
                       "import os; print(os.environ.get('SANDBOX_DEMO_API_KEY', 'absent'))"], ws, 30)
        assert r.stdout.strip() == "absent", "secrets must never reach repository code"
        try:
            run(None, [sys.executable, "-c", "raise SystemExit(3)"], ws, 30, check=True)
            raise AssertionError("check=True must raise")
        except subprocess.CalledProcessError as e:
            assert e.returncode == 3
        assert scratch_dir(ws) is None and which("git")
        open_run("x", ws)
        close_run("x")          # no-ops locally
    finally:
        os.environ.pop("SANDBOX_DEMO_API_KEY", None)
        shutil.rmtree(ws, ignore_errors=True)
    print("lib/sandbox.py OK (local mode)")


if __name__ == "__main__":
    demo()
