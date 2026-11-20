"""sandboxd — runs untrusted repository code in a per-run Docker container (platform plan, Phase 2).

The only Sentinel component that talks to Docker. stdlib only; it shells out to the `docker` CLI.

    SANDBOXD_TOKEN=<secret> python sandboxd/server.py          # listens on 127.0.0.1:8090

    POST /create   {run_id, workspace, cache_key?, cpus?, memory?}
    POST /exec     {run_id, argv, cwd, timeout, network?, env?} -> {returncode, stdout, stderr, timed_out}
    POST /destroy  {run_id}
    GET  /healthz
Every POST needs `Authorization: Bearer $SANDBOXD_TOKEN`.

Container rules: only the run's workspace (at /work) and its scratch dir (at /scratch) are mounted;
no host environment goes in (no LLM key, no DB credentials); the container is detached from every
network except while an exec that asked for it runs (the install phase); CPU, memory and process
count are capped; no-new-privileges, every capability dropped, read-only root filesystem.
Callers send host paths and this maps the ones under the workspace or scratch dir.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

WORK, SCRATCH = "/work", "/scratch"
SCRATCH_SUFFIX = ".sentinel-tmp"          # lib/sandbox.scratch_dir uses the same suffix
NETWORK = os.environ.get("SANDBOX_NETWORK", "sentinel-sandbox")
IMAGE = os.environ.get("SANDBOX_IMAGE", "sentinel-sandbox:latest")
RUNTIME = os.environ.get("SANDBOX_RUNTIME", "runc")          # runsc = gVisor (hosted)
PIDS_LIMIT = os.environ.get("SANDBOX_PIDS_LIMIT", "256")
OUT_CAP = 1_000_000                       # keep the last 1 MB of each stream
# Defence in depth: lib/sandbox already scrubs, but this process is the trust boundary.
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API|DATABASE_URL|NVIDIA|CRED)", re.I)
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CPUS = re.compile(r"^\d+(\.\d+)?$")
_MEM = re.compile(r"^\d+[kmg]?$", re.I)

_runs: Dict[str, Dict[str, Any]] = {}
_runs_lock = threading.Lock()


def _docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, timeout=timeout, check=False,
                          stdin=subprocess.DEVNULL)


def _must(p: subprocess.CompletedProcess, what: str) -> None:
    if p.returncode != 0:
        raise RuntimeError(f"{what}: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")


def _roots() -> List[str]:
    raw = os.environ.get("SANDBOX_WORKSPACE_ROOTS") or tempfile.gettempdir()
    return [os.path.normcase(os.path.realpath(r)) for r in raw.split(os.pathsep) if r]


# ---------------------------------------------------------------- host path -> container path
def to_container(s: str, mounts: List[tuple]) -> str:
    """Rewrite the first host mount path found in `s` (a whole arg, `--flag=<path>`, `json:<path>`).
    `mounts` is [(host_prefix, container_dir)], most specific first."""
    for host, cdir in mounts:
        i = s.find(host)
        if i < 0:
            continue
        rest = s[i + len(host):]
        if rest[:1] not in ("", "\\", "/"):
            continue                       # a sibling whose name merely starts with the prefix
        return s[:i] + cdir + rest.replace("\\", "/")
    return s


def map_argv(argv: List[str], mounts: List[tuple]) -> List[str]:
    out = [to_container(a, mounts) for a in argv]
    if out and out[0] == argv[0] and os.path.isabs(argv[0]):
        # a host interpreter/tool (sys.executable, a which() result): use the image's own
        out[0] = re.sub(r"\.(exe|cmd|bat)$", "", os.path.basename(argv[0]), flags=re.I)
    return out


def map_env(env: Dict[str, str], mounts: List[tuple]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in env.items():
        if _SECRET_ENV.search(k):
            continue
        if k == "PYTHONPYCACHEPREFIX":     # a fresh host temp dir per call (lib/pyexec): same idea here
            out[k] = f"/tmp/pyc-{uuid.uuid4().hex}"
        elif k.endswith("PATH"):
            parts = [(p, to_container(p, mounts)) for p in v.split(os.pathsep) if p]
            # host-only absolute entries mean nothing inside the container
            out[k] = ":".join(m for p, m in parts if m != p or not os.path.isabs(p))
        else:
            out[k] = to_container(v, mounts)
    return out


# ---------------------------------------------------------------- operations
def create(req: Dict[str, Any]) -> Dict[str, Any]:
    rid, ws = str(req.get("run_id") or ""), str(req.get("workspace") or "")
    if not _SAFE.match(rid):
        raise ValueError(f"unsafe run_id {rid!r}")
    real = os.path.realpath(ws)
    if not os.path.isdir(real):
        raise ValueError(f"workspace {ws!r} does not exist")
    if not any(os.path.normcase(real).startswith(r + os.sep) for r in _roots()):
        raise PermissionError(f"workspace {ws!r} is outside SANDBOX_WORKSPACE_ROOTS")
    cpus, mem = str(req.get("cpus") or "2"), str(req.get("memory") or "2g")
    if not (_CPUS.match(cpus) and _MEM.match(mem)):
        raise ValueError(f"bad quota cpus={cpus!r} memory={mem!r}")
    cache = req.get("cache_key")
    if cache is not None and not _SAFE.match(str(cache)):
        raise ValueError("bad cache_key")

    scratch = ws.rstrip("\\/") + SCRATCH_SUFFIX
    os.makedirs(scratch, exist_ok=True)
    real_scratch = os.path.realpath(scratch)
    # Files the container writes must stay deletable by whoever owns the workspace (Linux). Docker
    # Desktop bind mounts ignore ownership, so Windows/macOS just use an unprivileged uid.
    st = os.stat(real)
    user = f"{st.st_uid}:{st.st_gid}" if os.name != "nt" else "1000:1000"

    name = f"sentinel-run-{rid}"
    _docker("rm", "-f", name)              # a leftover from a crashed attempt with the same id
    args = ["create", "--name", name, "--label", "sentinel.sandbox=1",
            "--network", NETWORK, "--runtime", RUNTIME, "--user", user,
            "--cpus", cpus, "--memory", mem, "--memory-swap", mem, "--pids-limit", PIDS_LIMIT,
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=512m",
            "-e", "HOME=/tmp", "-v", f"{real}:{WORK}", "-v", f"{real_scratch}:{SCRATCH}",
            "-w", WORK]
    if cache:                               # per-repo, never shared between repos
        args += ["-v", f"sentinel-cache-{cache}:/cache",
                 "-e", "PIP_CACHE_DIR=/cache/pip", "-e", "npm_config_cache=/cache/npm"]
    _must(_docker(*args, IMAGE, "sleep", "infinity"), "docker create")
    try:
        # Born detached: network is opt-in per exec. Disconnect before start so no process ever
        # runs with a route out.
        _must(_docker("network", "disconnect", NETWORK, name), "network disconnect")
        _must(_docker("start", name), "docker start")
    except Exception:
        _docker("rm", "-f", name)
        raise
    mounts = [(scratch, SCRATCH), (real_scratch, SCRATCH), (ws.rstrip("\\/"), WORK), (real, WORK)]
    with _runs_lock:
        _runs[rid] = {"name": name, "mounts": mounts, "scratch": real_scratch, "net": False,
                      "lock": threading.Lock()}
    return {"container": name}


def exec_(req: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(req.get("run_id") or "")
    with _runs_lock:
        r = _runs.get(rid)
    if not r:
        raise LookupError(f"no sandbox open for run {rid!r}")
    argv = [str(a) for a in (req.get("argv") or [])]
    if not argv:
        raise ValueError("empty argv")
    ccwd = to_container(str(req.get("cwd") or WORK), r["mounts"])
    if not any(ccwd == d or ccwd.startswith(d + "/") for d in (WORK, SCRATCH)):
        raise PermissionError("cwd is outside the run's workspace")
    timeout = max(1, int(req.get("timeout") or 600))
    with r["lock"]:                        # one exec at a time per run: the network state is shared
        want = bool(req.get("network"))
        if want != r["net"]:
            _must(_docker("network", "connect" if want else "disconnect", NETWORK, r["name"]),
                  "network " + ("connect" if want else "disconnect"))
            r["net"] = want
        cmd = ["exec", "-w", ccwd]
        for k, v in map_env(req.get("env") or {}, r["mounts"]).items():
            cmd += ["-e", f"{k}={v}"]
        # `timeout` inside the container kills the process there; killing `docker exec` would not.
        cmd += [r["name"], "timeout", "-k", "5", str(timeout), *map_argv(argv, r["mounts"])]
        t0 = time.monotonic()
        try:
            p = _docker(*cmd, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return {"returncode": -9, "stdout": "", "stderr": "sandbox exec hung", "timed_out": True}
    elapsed = time.monotonic() - t0
    return {"returncode": p.returncode,
            "stdout": p.stdout[-OUT_CAP:].decode("utf-8", "replace"),
            "stderr": p.stderr[-OUT_CAP:].decode("utf-8", "replace"),
            "timed_out": p.returncode == 124 or elapsed >= timeout}


def destroy(req: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(req.get("run_id") or "")
    if not _SAFE.match(rid):
        raise ValueError(f"unsafe run_id {rid!r}")
    with _runs_lock:
        r = _runs.pop(rid, None)
    _docker("rm", "-f", f"sentinel-run-{rid}")
    if r:
        shutil.rmtree(r["scratch"], ignore_errors=True)
    return {"destroyed": bool(r)}


# ---------------------------------------------------------------- HTTP
_ROUTES = {"/create": create, "/exec": exec_, "/destroy": destroy}
_STATUS = {ValueError: 400, PermissionError: 403, LookupError: 404}


class Handler(BaseHTTPRequestHandler):
    token = ""

    def _send(self, code: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(200, {"ok": True}) if self.path == "/healthz" else self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        auth = self.headers.get("Authorization", "")
        if not self.token or not hmac.compare_digest(auth.encode(), f"Bearer {self.token}".encode()):
            return self._send(401, {"error": "unauthorized"})
        fn = _ROUTES.get(self.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self._send(200, fn(body))
        except Exception as e:
            code = next((c for t, c in _STATUS.items() if isinstance(e, t)), 500)
            self._send(code, {"error": str(e)[:500]})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("sandboxd: " + (fmt % args) + "\n")


def serve(host: str, port: int, token: str) -> ThreadingHTTPServer:
    Handler.token = token
    return ThreadingHTTPServer((host, port), Handler)


def prepare_docker() -> None:
    """Sandbox network (no container-to-container traffic) + reap containers from a previous life:
    run state is in memory, so after a restart nothing could ever destroy them."""
    if _docker("network", "inspect", NETWORK).returncode != 0:
        _must(_docker("network", "create", "-o", "com.docker.network.bridge.enable_icc=false",
                      NETWORK), "network create")
    old = _docker("ps", "-aq", "--filter", "label=sentinel.sandbox=1").stdout.decode().split()
    if old:
        _docker("rm", "-f", *old)


def main() -> int:
    token = os.environ.get("SANDBOXD_TOKEN", "")
    if not token:
        print("sandboxd: SANDBOXD_TOKEN is required", file=sys.stderr)
        return 2
    prepare_docker()
    host = os.environ.get("SANDBOXD_HOST", "127.0.0.1")
    port = int(os.environ.get("SANDBOXD_PORT", "8090"))
    srv = serve(host, port, token)
    print(f"sandboxd on http://{host}:{port} image={IMAGE} runtime={RUNTIME}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
