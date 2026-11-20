"""Sandbox escape suite (platform plan, Phase 2). NEEDS DOCKER — refuses to run without it.

Runs samples/sandbox-escape through the real test_runner path in SANDBOX_MODE=docker. Its tests try
to (a) read secrets from the environment, (b) reach the network during tests, (c) read another
live run's workspace and (d) fork-bomb. Each test passes only if its escape failed, so the gate is:
4 passed, 0 failed, the run completes, and the container still answers afterwards.

    docker build -t sentinel-sandbox:latest sandbox/
    PYTHONPATH=. python scripts/verify_sandbox.py

Starts its own sandboxd (random port + token) and stops it at the end. Exit 0 = PASS.
"""
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "samples", "sandbox-escape")
IMAGE = os.environ.get("SANDBOX_IMAGE", "sentinel-sandbox:latest")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_sandboxd(port: int, token: str) -> subprocess.Popen:
    env = {**os.environ, "SANDBOXD_TOKEN": token, "SANDBOXD_PORT": str(port),
           "SANDBOX_WORKSPACE_ROOTS": tempfile.gettempdir()}
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "sandboxd", "server.py")], env=env)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("sandboxd did not come up")


def main() -> int:
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("SKIP: Docker engine not reachable — this suite needs Docker.")
        return 2
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        print(f"SKIP: image {IMAGE} missing — run: docker build -t {IMAGE} sandbox/")
        return 2

    port, token = _free_port(), secrets.token_hex(16)
    sbd = _start_sandboxd(port, token)
    canary = secrets.token_hex(8)
    os.environ.update({
        "SANDBOX_MODE": "docker", "SANDBOXD_URL": f"http://127.0.0.1:{port}",
        "SANDBOXD_TOKEN": token,
        # what a careless runner would hand to repository code
        "NVIDIA_API_KEY": "nvapi-canary-" + canary, "DATABASE_URL": "postgresql://canary:x@db/x",
        "SENTINEL_ESCAPE_CANARY": "canary-" + canary,
    })
    from coded_tools.sentinel.test_runner_tool import TestRunnerTool
    from lib import sandbox

    run_a, run_b = uuid.uuid4().hex, uuid.uuid4().hex
    ws_a = tempfile.mkdtemp(prefix="sentinel-esc-a-")
    ws_b = tempfile.mkdtemp(prefix="sentinel-esc-b-")
    ok = True
    try:
        # run B: a concurrent run with something worth stealing
        marker = f"SENTINEL_ESCAPE_MARKER_{canary}"
        with open(os.path.join(ws_b, marker), "w", encoding="utf-8") as fh:
            fh.write("run B's private data\n")
        sandbox.open_run(run_b, ws_b, "sandbox-escape-b")

        # run A: the hostile repository
        shutil.copytree(FIXTURE, ws_a, dirs_exist_ok=True)
        for name, value in (("escape_target.txt", os.path.join(ws_b, marker)),
                            ("escape_marker_name.txt", marker),
                            ("escape_canary_value.txt", canary)):
            with open(os.path.join(ws_a, name), "w", encoding="utf-8") as fh:
                fh.write(value)
        sandbox.open_run(run_a, ws_a, "sandbox-escape")

        # positive control: the same container DOES reach the network when the install phase asks
        ctl = sandbox.run(run_a, ["python", "-c", "import socket; socket.getaddrinfo('pypi.org', 443)"],
                          ws_a, 30, network=True)
        print(f"[control] install-phase network: {'reachable' if ctl.returncode == 0 else 'UNREACHABLE'}"
              + ("" if ctl.returncode == 0 else " (host offline? escape (b) then proves less)"))

        sly = {"run_id": run_a, "repo_workspace": ws_a,
               "event": {"repo": {"name": "sandbox-escape"}, "change": {}}}
        res = TestRunnerTool().invoke({}, sly)
        if isinstance(res, str):
            print(f"[run] FAIL: test_runner returned {res}")
            return 1
        for c in res["cases"]:
            print(f"[escape] {c['test_id']:<60} {'BLOCKED' if c['status'] == 'passed' else 'ESCAPED'}"
                  + (f"  -- {c.get('failure_message', '')[:200]}" if c["status"] != "passed" else ""))
        t = res["totals"]
        blocked = t.get("passed", 0) == 4 and not t.get("failed") and not t.get("errors")
        print(f"[run] totals={t} timed_out={res['timed_out']} "
              f"stage_failure={res.get('stage_failure')}")
        ok = blocked and not res["timed_out"]

        alive = sandbox.run(run_a, ["python", "-c", "print('alive')"], ws_a, 30)
        print(f"[run] container still answers after the escapes: {alive.stdout.strip() == 'alive'}")
        ok = ok and alive.stdout.strip() == "alive"
    finally:
        sandbox.close_run(run_a)
        sandbox.close_run(run_b)
        left = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=sentinel-run-{run_a}"],
                              capture_output=True, text=True).stdout.strip()
        gone = not left and not os.path.exists(ws_a + sandbox.SCRATCH_SUFFIX)
        print(f"[close] container + scratch removed: {gone}")
        ok = ok and gone
        sbd.terminate()
        shutil.rmtree(ws_a, ignore_errors=True)
        shutil.rmtree(ws_b, ignore_errors=True)

    print("SANDBOX ESCAPE SUITE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
