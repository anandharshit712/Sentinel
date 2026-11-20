"""lib.sandbox in both modes, and sandboxd's container rules — no Docker needed.

Docker mode runs the real sandboxd HTTP server in-process with its `docker` CLI calls faked, so
these tests check exactly what would be sent to Docker: the flags, the mounts, the path mapping,
the network switching and the environment that reaches the container.
"""
import os
import subprocess
import sys
import threading

import pytest

from lib import sandbox
from sandboxd import server as sbd


# ---------------------------------------------------------------- local mode
def test_local_scrubs_secrets_and_keeps_subprocess_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    code = "import os; print(os.environ.get('NVIDIA_API_KEY'), os.environ.get('DATABASE_URL'))"
    r = sandbox.run(None, [sys.executable, "-c", code], str(tmp_path), 30)
    assert r.returncode == 0 and r.stdout.strip() == "None None"

    # an explicit env is scrubbed too (blind_oracle used to pass an unscrubbed one)
    r = sandbox.run(None, [sys.executable, "-c", code], str(tmp_path), 30,
                    env={**os.environ, "NVIDIA_API_KEY": "again"})
    assert r.stdout.strip() == "None None"

    with pytest.raises(subprocess.CalledProcessError):
        sandbox.run(None, [sys.executable, "-c", "raise SystemExit(2)"], str(tmp_path), 30, check=True)
    with pytest.raises(subprocess.TimeoutExpired):
        sandbox.run(None, [sys.executable, "-c", "import time; time.sleep(5)"], str(tmp_path), 0.5)
    assert sandbox.scratch_dir(str(tmp_path)) is None
    sandbox.open_run("r1", str(tmp_path))      # no-ops, no sandboxd needed
    sandbox.close_run("r1")


# ---------------------------------------------------------------- path mapping
def test_path_mapping():
    ws = os.path.join("C:" + os.sep, "tmp", "ws")
    mounts = [(ws + ".sentinel-tmp", "/scratch"), (ws, "/work")]
    assert sbd.to_container(ws, mounts) == "/work"
    assert sbd.to_container(os.path.join(ws, "tests", "t.py"), mounts) == "/work/tests/t.py"
    assert sbd.to_container("--outputFile=" + os.path.join(ws + ".sentinel-tmp", "j.json"),
                            mounts) == "--outputFile=/scratch/j.json"
    assert sbd.to_container("json:" + os.path.join(ws, "c.json"), mounts) == "json:/work/c.json"
    assert sbd.to_container(ws + "2" + os.sep + "x", mounts) == ws + "2" + os.sep + "x", \
        "a sibling sharing the prefix is not the workspace"
    assert sbd.to_container("-q", mounts) == "-q"

    host_py = os.path.abspath(os.path.join(os.sep, "opt", "venv", "bin", "python.exe"))
    assert sbd.map_argv([host_py, "-m", "pytest"], mounts)[0] == "python"
    venv_py = os.path.join(ws, ".sentinel-venv", "bin", "python")
    assert sbd.map_argv([venv_py], mounts) == ["/work/.sentinel-venv/bin/python"]

    env = sbd.map_env({"PYTHONPATH": os.pathsep.join([os.path.join(ws, "src"),
                                                     os.path.abspath(os.sep + "host-only")]),
                       "PYTHONPYCACHEPREFIX": os.path.abspath(os.sep + "hosttmp"),
                       "PYTHONDONTWRITEBYTECODE": "1", "MY_API_KEY": "x"}, mounts)
    assert env["PYTHONPATH"] == "/work/src", env
    assert env["PYTHONPYCACHEPREFIX"].startswith("/tmp/pyc-")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1" and "MY_API_KEY" not in env


# ---------------------------------------------------------------- docker mode, Docker faked
class FakeDocker:
    def __init__(self):
        self.calls = []
        self.exec_rc = 0

    def __call__(self, *args, timeout=120):
        self.calls.append(list(args))
        rc = self.exec_rc if args[0] == "exec" else 0
        return subprocess.CompletedProcess(["docker", *args], rc, b"out", b"")

    def find(self, verb):
        return [c for c in self.calls if c[0] == verb]


@pytest.fixture
def docker_mode(tmp_path, monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(sbd, "_docker", fake)
    monkeypatch.setattr(sbd, "_runs", {})
    srv = sbd.serve("127.0.0.1", 0, "t0ken")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.setenv("SANDBOXD_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("SANDBOXD_TOKEN", "t0ken")
    monkeypatch.setenv("SANDBOX_WORKSPACE_ROOTS", str(tmp_path))
    ws = tmp_path / "run-a"
    ws.mkdir()
    yield fake, str(ws)
    srv.shutdown()
    srv.server_close()


def test_docker_create_applies_container_rules(docker_mode):
    fake, ws = docker_mode
    sandbox.open_run("run-a", ws, "python-payments-service")
    create = fake.find("create")[-1]
    flag = lambda f: create[create.index(f) + 1]          # noqa: E731
    assert flag("--cpus") == "2" and flag("--memory") == "2g" and flag("--memory-swap") == "2g"
    assert flag("--pids-limit") == sbd.PIDS_LIMIT
    assert flag("--security-opt") == "no-new-privileges" and flag("--cap-drop") == "ALL"
    assert "--read-only" in create and flag("--runtime") == sbd.RUNTIME
    vols = [create[i + 1] for i, a in enumerate(create) if a == "-v"]
    assert any(v.endswith(":/work") and v.startswith(os.path.realpath(ws)) for v in vols)
    assert any(v.endswith(":/scratch") for v in vols)
    cache = [v for v in vols if v.endswith(":/cache")]
    assert len(cache) == 1 and cache[0].startswith("sentinel-cache-")
    envs = [create[i + 1] for i, a in enumerate(create) if a == "-e"]
    assert not any(e.split("=")[0] in os.environ for e in envs if not e.startswith("HOME=")), \
        "no host environment goes into the container"
    # born detached: disconnect happens before start
    order = [c[0] if c[0] != "network" else "network " + c[1] for c in fake.calls]
    assert order.index("network disconnect") < order.index("start")
    assert os.path.isdir(ws + sbd.SCRATCH_SUFFIX)

    # the per-repo cache is never shared between repos; no repo name, no cache
    sandbox.open_run("run-b", ws, "node-catalog-service")
    other = [v for v in fake.find("create")[-1] if v.endswith(":/cache")]
    assert other and other != cache
    sandbox.open_run("run-c", ws)
    assert not [v for v in fake.find("create")[-1] if v.endswith(":/cache")]


def test_docker_exec_maps_paths_switches_network_and_strips_env(docker_mode, monkeypatch):
    fake, ws = docker_mode
    sandbox.open_run("run-a", ws)
    scratch = sandbox.scratch_dir(ws)
    assert scratch == ws + sandbox.SCRATCH_SUFFIX

    monkeypatch.setenv("NVIDIA_API_KEY", "nv-secret")
    env = {**os.environ, "PYTHONPATH": os.path.join(ws, "src"), "NVIDIA_API_KEY": "nv-secret"}
    r = sandbox.run("run-a", [sys.executable, "-m", "pytest", os.path.join(ws, "tests"),
                              "--junitxml", os.path.join(scratch, "j.xml")], ws, 30, env=env)
    assert r.returncode == 0 and r.stdout == "out"
    ex = fake.find("exec")[-1]
    assert ex[ex.index("-w") + 1] == "/work"
    sent_env = [ex[i + 1] for i, a in enumerate(ex) if a == "-e"]
    assert sent_env == ["PYTHONPATH=/work/src"], sent_env      # only overrides; secrets never
    tail = ex[ex.index("sentinel-run-run-a") + 1:]
    assert tail[:4] == ["timeout", "-k", "5", "30"]
    assert tail[4:] == ["python", "-m", "pytest", "/work/tests", "--junitxml", "/scratch/j.xml"]
    assert not [c for c in fake.calls[-2:] if c[0] == "network"], "already detached"

    sandbox.run("run-a", ["npm", "install"], ws, 30, network=True)
    assert fake.calls[-2][:2] == ["network", "connect"] and fake.calls[-1][0] == "exec"
    sandbox.run("run-a", ["npx", "jest"], ws, 30)
    assert fake.calls[-2][:2] == ["network", "disconnect"], "tests run with the network off"

    fake.exec_rc = 124
    with pytest.raises(subprocess.TimeoutExpired):
        sandbox.run("run-a", ["python", "-c", "pass"], ws, 30)
    fake.exec_rc = 1
    with pytest.raises(subprocess.CalledProcessError):
        sandbox.run("run-a", ["python", "-c", "pass"], ws, 30, check=True)

    sandbox.close_run("run-a")
    assert fake.find("rm")[-1] == ["rm", "-f", "sentinel-run-run-a"]
    assert not os.path.exists(scratch)
    sandbox.close_run("run-a")                                  # idempotent


def test_docker_refusals(docker_mode, tmp_path, monkeypatch):
    fake, ws = docker_mode
    with pytest.raises(RuntimeError, match="run_id"):
        sandbox.run(None, ["python", "-c", "pass"], ws, 30)    # never falls back to the host
    with pytest.raises(RuntimeError, match="404"):
        sandbox.run("never-opened", ["python"], ws, 30)
    with pytest.raises(RuntimeError, match="403"):              # outside the allowed roots
        sandbox.open_run("run-x", os.path.dirname(os.path.realpath(sys.executable)))
    with pytest.raises(RuntimeError, match="400"):
        sandbox.open_run("../evil", ws)
    sandbox.open_run("run-a", ws)
    with pytest.raises(RuntimeError, match="403"):              # cwd outside the run's mounts
        sandbox.run("run-a", ["python"], str(tmp_path), 30)
    monkeypatch.setenv("SANDBOXD_TOKEN", "wrong")
    with pytest.raises(RuntimeError, match="401"):
        sandbox.run("run-a", ["python"], ws, 30)


def test_test_runner_phases_network(monkeypatch, tmp_path):
    """Install steps ask for the network; test steps never do."""
    from coded_tools.sentinel import test_runner_tool as tr
    calls = []

    def fake_run(rid, argv, cwd, timeout, network=False, env=None, check=False):
        calls.append((rid, argv[1:3], network))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tr.sandbox, "run", fake_run)
    (tmp_path / "requirements.txt").write_text("pytest\n")
    tr._prepare_venv("rid-1", str(tmp_path), ["."], {}, 60)
    tr._npm_install("rid-1", "npm", str(tmp_path), {}, 60)
    assert calls and all(net for _, _, net in calls), calls
    calls.clear()
    tr._collect_total("rid-1", "python", str(tmp_path), {}, 60)
    tr._has_pytest_cov("rid-1", "python", str(tmp_path), {})
    tr._list_jest_files("rid-1", "npx", str(tmp_path), {}, 60)
    assert calls and not any(net for _, _, net in calls), calls
    assert {rid for rid, _, _ in calls} == {"rid-1"}
