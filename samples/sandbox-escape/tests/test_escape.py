"""Escape attempts. Each test passes only if the attempt FAILED.

scripts/verify_sandbox.py plants, before this runs:
  * secrets in the environment of the process that starts the run (NVIDIA_API_KEY, DATABASE_URL)
    and a non-secret-named canary (SENTINEL_ESCAPE_CANARY) — nothing from the host should arrive;
  * escape_target.txt — the host path of a marker file inside ANOTHER run's workspace;
  * escape_marker_name.txt — that marker's file name, to hunt for it anywhere visible.
"""
import os
import signal
import socket
import time

import pytest

CANARY_KEYS = ("NVIDIA_API_KEY", "DATABASE_URL", "SANDBOXD_TOKEN", "SENTINEL_ESCAPE_CANARY")


def _read(name):
    with open(name, encoding="utf-8") as fh:
        return fh.read().strip()


def test_a_no_secrets_in_environment():
    leaked = [k for k in CANARY_KEYS if k in os.environ]
    assert not leaked, f"host environment leaked into the sandbox: {leaked}"
    canary = b"canary-" + _read("escape_canary_value.txt").encode()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/environ", "rb") as fh:
                    assert canary not in fh.read(), f"canary visible in /proc/{pid}/environ"
            except OSError:
                pass


def test_b_no_network_during_tests():
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 443), ("host.docker.internal", 5432)):
        with pytest.raises(OSError):
            socket.create_connection((host, port), timeout=3).close()
    with pytest.raises(OSError):
        socket.getaddrinfo("pypi.org", 443)


def test_c_cannot_read_another_runs_workspace():
    target, marker = _read("escape_target.txt"), _read("escape_marker_name.txt")
    with pytest.raises(OSError):
        _read(target)
    # Not by its host path, and not by searching: walk everything this process can see.
    found = []
    for root, dirs, files in os.walk("/"):
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in ("/proc", "/sys", "/dev")]
        if marker in files:
            found.append(os.path.join(root, marker))
    assert not found, f"another run's file is visible: {found}"


def test_d_fork_bomb_is_contained():
    pids, hit_limit = [], False
    try:
        for _ in range(4000):
            pid = os.fork()
            if pid == 0:                 # child: idle, then leave without running pytest's teardown
                time.sleep(30)
                os._exit(0)
            pids.append(pid)
    except OSError:
        hit_limit = True
    finally:
        for p in pids:
            os.kill(p, signal.SIGKILL)
        for p in pids:
            os.waitpid(p, 0)
    assert hit_limit, f"spawned {len(pids)} processes without hitting a limit"
