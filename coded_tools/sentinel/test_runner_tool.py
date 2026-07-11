"""test_runner_tool (04 §5.9, A4) — executes the selected TestPlan and returns TestResults.

Reads test_plan from sly_data, detects the repo's runner, runs the selected node-ids as a
subprocess in the workspace with a scrubbed environment (no tokens/secrets) and a timeout, parses
the result into the test_results contract and writes it to sly_data. Python/pytest (JUnit XML) and
JS-TS/jest (`--json`) are both implemented. Never raises: a timeout or a missing runner becomes a
stage_failure in the contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.test_runner")

_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API|DATABASE_URL|NVIDIA|CRED)", re.I)
_STATUS_TAG = {"failure": "failed", "error": "error", "skipped": "skipped"}
_JEST_STATUS = {"passed": "passed", "failed": "failed", "pending": "skipped", "todo": "skipped"}
_EMPTY_TOTALS = {"passed": 0, "failed": 0, "skipped": 0}
# markers that identify a Python project dir (for monorepo detection + per-repo dep install)
_PY_MARKERS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "pytest.ini", "tox.ini")
# dirs never worth scanning/installing (vendored, build output, virtualenvs, caches)
_PRUNE_DIRS = {"node_modules", "__pycache__", "site-packages", "dist", "build", "vendor",
               "venv", ".venv", ".tox", ".git", ".mypy_cache", ".pytest_cache"}


def _scrubbed_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


def _project_dirs(repo: str, configured: Any, max_depth: int = 4) -> List[str]:
    """Auto-scan for Python project dirs (repo-relative; '.' = root) at any nesting depth.

    Walks the tree, skipping vendored/build/venv/dot dirs, and records every dir holding a marker
    (pyproject/requirements/setup/pytest.ini/tox.ini). A matched dir isn't descended into — its
    sub-packages install with its `pip install -e .`. Handles monorepos (ORION: arep_implementation,
    orion-sdk) with no per-repo config. `configured` (repo_config test_project_dirs) still overrides.
    """
    if configured:
        return [d for d in configured if os.path.isdir(os.path.join(repo, d))]
    repo = os.path.abspath(repo)
    found: List[str] = []
    for dirpath, dirnames, files in os.walk(repo):
        rel = os.path.relpath(dirpath, repo).replace("\\", "/")
        depth = 0 if rel == "." else rel.count("/") + 1
        dirnames[:] = [] if depth >= max_depth else sorted(
            d for d in dirnames if d not in _PRUNE_DIRS and not d.startswith("."))
        if any(m in files for m in _PY_MARKERS):
            found.append("." if rel == "." else rel)
            dirnames[:] = []  # matched project — don't descend into its sub-packages
    return found


def _detect_runner(repo: str) -> str:
    if _project_dirs(repo, None):
        return "pytest"
    if os.path.isfile(os.path.join(repo, "package.json")):
        return "jest"
    return "none_detected"


def _venv_python(venv: str) -> str:
    win = os.path.join(venv, "Scripts", "python.exe")
    return win if os.path.exists(win) else os.path.join(venv, "bin", "python")


def _prepare_venv(repo: str, proj_dirs: List[str], env: Dict[str, str], timeout: int) -> str:
    """Create a per-run venv and install each project dir's deps; return its python path.

    SECURITY: `pip install -e .` runs the repo's build backend (arbitrary code) on the Gateway
    host, so this is gated behind repo_config `install_deps: true` (an explicit per-repo allowlist),
    never on by default. Raises on venv/pip failure or timeout; the caller turns that into a
    stage_failure rather than crashing the run.
    """
    venv = os.path.join(repo, ".sentinel-venv")  # dot-prefixed -> pytest's default norecursedirs skips it
    subprocess.run([sys.executable, "-m", "venv", venv], check=True, capture_output=True,
                   encoding="utf-8", errors="replace", timeout=min(timeout, 180))
    py = _venv_python(venv)
    deadline = time.perf_counter() + timeout

    def _pip(*a: str, cwd: str) -> None:
        remaining = int(deadline - time.perf_counter())
        if remaining <= 0:
            raise subprocess.TimeoutExpired("pip", timeout)
        subprocess.run([py, "-m", "pip", "install", "-q", *a], cwd=cwd, env=env,
                       check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=remaining)

    _pip("--upgrade", "pip", cwd=repo)
    _pip("pytest", cwd=repo)  # ensure a runner even if the repo doesn't declare it
    for d in proj_dirs:
        cwd = repo if d == "." else os.path.join(repo, d)
        if os.path.isfile(os.path.join(cwd, "requirements.txt")):
            _pip("-r", "requirements.txt", cwd=cwd)
        if any(os.path.isfile(os.path.join(cwd, m)) for m in ("pyproject.toml", "setup.py", "setup.cfg")):
            _pip("-e", ".", cwd=cwd)
    return py


def _collect_total(py: str, repo: str, env: Dict[str, str], timeout: int) -> int:
    """Total tests pytest would collect for the WHOLE suite — the denominator for selection."""
    try:
        r = subprocess.run([py, "-m", "pytest", "--collect-only", "-q",
                            "-p", "no:cacheprovider"], cwd=repo, env=env,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=min(timeout, 120), check=False)
        m = re.search(r"(\d+)\s+tests?\s+collected", r.stdout)
        if m:
            return int(m.group(1))
        return sum(1 for ln in r.stdout.splitlines() if "::" in ln)
    except Exception:
        return 0


def _parse_junit(path: str) -> tuple:
    totals = dict(_EMPTY_TOTALS)
    cases: List[Dict[str, Any]] = []
    root = ET.parse(path).getroot()
    for suite in root.iter("testsuite"):
        for tc in suite.iter("testcase"):
            name, classname = tc.get("name", "?"), tc.get("classname", "")
            tid = f"{classname}::{name}" if classname else name
            status, msg = "passed", ""
            for tag, mapped in _STATUS_TAG.items():
                el = tc.find(tag)
                if el is not None:
                    status = mapped
                    msg = el.get("message", "") or (el.text or "")
                    break
            totals["errors" if status == "error" else status] = totals.get(
                "errors" if status == "error" else status, 0) + 1
            case = {"test_id": tid, "status": status,
                    "duration_ms": int(float(tc.get("time", 0)) * 1000)}
            if msg:
                case["failure_message"] = msg[:2000]
            cases.append(case)
    return totals, cases


def _resolve_bin(name: str) -> Optional[str]:
    return shutil.which(name)


def _npm_install(npm: str, repo: str, env: Dict[str, str], timeout: int) -> None:
    """SECURITY: `npm install` runs the repo's lifecycle scripts (arbitrary code) on the Gateway
    host — same class of risk as `_prepare_venv`'s `pip install -e .`, gated the same way behind
    repo_config `install_deps`."""
    subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=repo, env=env,
                   check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                   stdin=subprocess.DEVNULL)


def _list_jest_files(npx: str, repo: str, env: Dict[str, str], timeout: int) -> List[str]:
    """Total test FILES jest would run — the denominator for selection (JS selection is
    file-granularity, unlike pytest's per-test-id granularity; see test_mapper_tool)."""
    try:
        # --no-install + closed stdin: never let npx prompt to download jest (would block on
        # stdin forever, orphaning a node child that deadlocks subprocess.run's post-kill pipe
        # read on Windows). If jest isn't installed, npx exits non-zero → we return [] fast.
        r = subprocess.run([npx, "--no-install", "jest", "--listTests"], cwd=repo, env=env,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=min(timeout, 120), check=False,
                           stdin=subprocess.DEVNULL)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def _parse_jest_json(path: str, repo: str) -> tuple:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    totals = dict(_EMPTY_TOTALS)
    cases: List[Dict[str, Any]] = []
    for suite in data.get("testResults", []):
        rel = os.path.relpath(suite.get("name", ""), repo).replace("\\", "/")
        for a in suite.get("assertionResults", []):
            status = _JEST_STATUS.get(a.get("status"), "failed")
            totals[status] = totals.get(status, 0) + 1
            case = {"test_id": f"{rel}::{a.get('fullName') or a.get('title', '?')}",
                    "status": status, "duration_ms": int(a.get("duration") or 0)}
            msgs = a.get("failureMessages") or []
            if msgs:
                case["failure_message"] = "\n".join(msgs)[:2000]
            cases.append(case)
    return totals, cases


class TestRunnerTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo, _base, _head, repo_name = run_inputs(sly_data, args)
            if not repo or not os.path.isdir(repo):
                return "Error: missing/invalid repo_workspace"
            plan = sly_data.get("test_plan") or {}
            ids = list(dict.fromkeys(
                [s["test_id"] for s in plan.get("selected", [])] + list(plan.get("smoke_set", []))))

            runner = _detect_runner(repo)
            cfg = self._repo_cfg(repo_name)
            env = _scrubbed_env()

            if runner == "pytest":
                payload = self._run_pytest(repo, cfg, env, ids)
            elif runner == "jest":
                payload = self._run_jest(repo, cfg, env, ids)
            else:
                payload = {"runner": runner, "command": "", "totals": dict(_EMPTY_TOTALS),
                           "cases": [], "duration_seconds": 0.0, "timed_out": False,
                           "stage_failure": f"runner {runner} not supported yet"}
            return self._store(sly_data, run_id, payload)
        except Exception as e:
            return f"Error: {e}"

    def _run_pytest(self, repo: str, cfg: Dict[str, Any], env: Dict[str, str],
                    ids: List[str]) -> Dict[str, Any]:
        timeout = int(cfg.get("test_timeout_seconds", 900))

        # By default run with the Gateway's own interpreter (deps already present, e.g. sample
        # repos). Real external repos rarely share the Gateway's env, so repo_config may opt in
        # to a per-run venv with the repo's deps installed (see _prepare_venv security note).
        py = sys.executable
        if cfg.get("install_deps"):
            proj = _project_dirs(repo, cfg.get("test_project_dirs"))
            itimeout = int(cfg.get("install_timeout_seconds", 600))
            try:
                py = _prepare_venv(repo, proj, env, itimeout)
            except subprocess.TimeoutExpired:
                return {"runner": "pytest", "command": "", "totals": dict(_EMPTY_TOTALS),
                       "cases": [], "duration_seconds": 0.0, "timed_out": False,
                       "stage_failure": f"dependency install exceeded {itimeout}s"}
            except subprocess.CalledProcessError as e:
                tail = ((e.stderr or e.stdout or "")[-1000:]).strip()
                return {"runner": "pytest", "command": "", "totals": dict(_EMPTY_TOTALS),
                       "cases": [], "duration_seconds": 0.0, "timed_out": False,
                       "stage_failure": f"dependency install failed: {tail}"}

        suite_total = _collect_total(py, repo, env, timeout)
        # Selection story: run only the mapped ids; if nothing mapped, fall back to the full
        # suite but LABEL it honestly (never silently "select" everything).
        selection_mode = "subset" if ids else "full_suite_fallback"
        fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="sentinel-junit-")
        os.close(fd)
        cmd = [py, "-m", "pytest", *ids, "--junitxml", xml_path, "-q", "-p", "no:cacheprovider"]
        start = time.perf_counter()
        try:
            subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, check=False)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = round(time.perf_counter() - start, 3)

        payload: Dict[str, Any] = {"runner": "pytest", "command": " ".join(cmd),
                                   "duration_seconds": elapsed, "timed_out": timed_out,
                                   "suite_total": suite_total, "selection_mode": selection_mode,
                                   "selected_ids": ids}
        if timed_out:
            payload.update(totals=dict(_EMPTY_TOTALS), cases=[],
                           stage_failure=f"test run exceeded {timeout}s")
        elif os.path.getsize(xml_path) > 0:
            totals, cases = _parse_junit(xml_path)
            payload.update(totals=totals, cases=cases)
        else:
            payload.update(totals=dict(_EMPTY_TOTALS), cases=[],
                           stage_failure="no JUnit output (runner crashed)")
        executed = sum(payload["totals"].values())
        payload["executed"] = executed
        payload["excluded"] = max(0, suite_total - executed)
        os.unlink(xml_path)
        return payload

    def _run_jest(self, repo: str, cfg: Dict[str, Any], env: Dict[str, str],
                 ids: List[str]) -> Dict[str, Any]:
        timeout = int(cfg.get("test_timeout_seconds", 900))
        npx, npm = _resolve_bin("npx"), _resolve_bin("npm")
        if not npx or not npm:
            return {"runner": "jest", "command": "", "totals": dict(_EMPTY_TOTALS), "cases": [],
                   "duration_seconds": 0.0, "timed_out": False,
                   "stage_failure": "node/npm not found on PATH"}

        if cfg.get("install_deps") and not os.path.isdir(os.path.join(repo, "node_modules")):
            itimeout = int(cfg.get("install_timeout_seconds", 600))
            try:
                _npm_install(npm, repo, env, itimeout)
            except subprocess.TimeoutExpired:
                return {"runner": "jest", "command": "", "totals": dict(_EMPTY_TOTALS), "cases": [],
                       "duration_seconds": 0.0, "timed_out": False,
                       "stage_failure": f"dependency install exceeded {itimeout}s"}
            except subprocess.CalledProcessError as e:
                tail = ((e.stderr or e.stdout or "")[-1000:]).strip()
                return {"runner": "jest", "command": "", "totals": dict(_EMPTY_TOTALS), "cases": [],
                       "duration_seconds": 0.0, "timed_out": False,
                       "stage_failure": f"dependency install failed: {tail}"}

        suite_total = len(_list_jest_files(npx, repo, env, timeout))  # file-granularity denominator
        selection_mode = "subset" if ids else "full_suite_fallback"
        fd, json_path = tempfile.mkstemp(suffix=".json", prefix="sentinel-jest-")
        os.close(fd)
        # Bare positional args, not `--testPathPatterns=<a>|<b>`: jest OR's multiple bare path
        # patterns natively (stable across Jest majors, no version-flag split needed), and a
        # `|`-joined single arg breaks on Windows — npx's .CMD shim runs through cmd.exe, and
        # Python's list2cmdline doesn't escape `|` for that inner shell, so it truncates there.
        cmd = [npx, "--no-install", "jest", "--json", f"--outputFile={json_path}", *ids]
        start = time.perf_counter()
        try:
            subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, check=False, stdin=subprocess.DEVNULL)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = round(time.perf_counter() - start, 3)

        payload: Dict[str, Any] = {"runner": "jest", "command": " ".join(cmd),
                                   "duration_seconds": elapsed, "timed_out": timed_out,
                                   "suite_total": suite_total, "selection_mode": selection_mode,
                                   "selected_ids": ids}
        if timed_out:
            payload.update(totals=dict(_EMPTY_TOTALS), cases=[],
                           stage_failure=f"test run exceeded {timeout}s")
        elif os.path.exists(json_path) and os.path.getsize(json_path) > 0:
            totals, cases = _parse_jest_json(json_path, repo)
            payload.update(totals=totals, cases=cases)
        else:
            payload.update(totals=dict(_EMPTY_TOTALS), cases=[],
                           stage_failure="no jest JSON output (runner crashed)")
        executed = sum(payload["totals"].values())
        payload["executed"] = executed
        payload["excluded"] = max(0, suite_total - executed)
        if os.path.exists(json_path):
            os.unlink(json_path)
        return payload

    def _repo_cfg(self, repo_name) -> Dict[str, Any]:
        """Per-repo config dict (test_timeout_seconds, install_deps, test_project_dirs, ...)."""
        try:
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            return ((cfg.get("repos") or {}).get(repo_name) or {})
        except Exception:
            return {}

    def _store(self, sly_data, run_id, payload) -> Dict[str, Any]:
        wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by="test_runner")
        contracts.validate("test_results", wrapped)
        sly_data["test_results"] = wrapped
        logger.info("run %s: test_runner %s totals=%s timed_out=%s", run_id,
                    payload["runner"], payload["totals"], payload["timed_out"])
        return payload

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
