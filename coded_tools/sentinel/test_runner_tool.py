"""test_runner_tool (04 §5.9, A4) — executes the selected TestPlan and returns TestResults.

Reads test_plan from sly_data, detects the repo's runner, runs the selected node-ids as a
subprocess in the workspace with a scrubbed environment (no tokens/secrets) and a timeout, parses
the JUnit XML into the test_results contract and writes it to sly_data. Python/pytest is
implemented; jest is detected but deferred with the node sample repo. Never raises: a timeout or a
missing runner becomes a stage_failure in the contract.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts
from lib.workspace import run_inputs

logger = logging.getLogger("coded_tools.test_runner")

_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API|DATABASE_URL|NVIDIA|CRED)", re.I)
_STATUS_TAG = {"failure": "failed", "error": "error", "skipped": "skipped"}


def _scrubbed_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


def _detect_runner(repo: str) -> str:
    for f in ("pytest.ini", "pyproject.toml", "requirements.txt", "setup.cfg"):
        if os.path.isfile(os.path.join(repo, f)):
            return "pytest"
    if os.path.isfile(os.path.join(repo, "package.json")):
        return "jest"  # deferred with the node sample repo
    return "none_detected"


def _collect_total(repo: str, env: Dict[str, str], timeout: int) -> int:
    """Total tests pytest would collect for the WHOLE suite — the denominator for selection."""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                            "-p", "no:cacheprovider"], cwd=repo, env=env,
                           capture_output=True, text=True, timeout=min(timeout, 120), check=False)
        m = re.search(r"(\d+)\s+tests?\s+collected", r.stdout)
        if m:
            return int(m.group(1))
        return sum(1 for ln in r.stdout.splitlines() if "::" in ln)
    except Exception:
        return 0


def _parse_junit(path: str) -> tuple:
    totals = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
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
            totals["errors" if status == "error" else status] += 1
            case = {"test_id": tid, "status": status,
                    "duration_ms": int(float(tc.get("time", 0)) * 1000)}
            if msg:
                case["failure_message"] = msg[:2000]
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
            if runner != "pytest":
                return self._store(sly_data, run_id, {
                    "runner": runner, "command": "", "totals": {"passed": 0, "failed": 0, "skipped": 0},
                    "cases": [], "duration_seconds": 0.0, "timed_out": False,
                    "stage_failure": f"runner {runner} not supported yet"})

            timeout = self._timeout(repo_name)
            env = _scrubbed_env()
            suite_total = _collect_total(repo, env, timeout)
            # Selection story: run only the mapped ids; if nothing mapped, fall back to the full
            # suite but LABEL it honestly (never silently "select" everything).
            selection_mode = "subset" if ids else "full_suite_fallback"
            fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="sentinel-junit-")
            os.close(fd)
            cmd = [sys.executable, "-m", "pytest", *ids,
                   "--junitxml", xml_path, "-q", "-p", "no:cacheprovider"]
            start = time.perf_counter()
            try:
                subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                               text=True, timeout=timeout, check=False)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
            elapsed = round(time.perf_counter() - start, 3)

            payload: Dict[str, Any] = {"runner": "pytest", "command": " ".join(cmd),
                                       "duration_seconds": elapsed, "timed_out": timed_out,
                                       "suite_total": suite_total, "selection_mode": selection_mode,
                                       "selected_ids": ids}
            if timed_out:
                payload.update(totals={"passed": 0, "failed": 0, "skipped": 0}, cases=[],
                               stage_failure=f"test run exceeded {timeout}s")
            elif os.path.getsize(xml_path) > 0:
                totals, cases = _parse_junit(xml_path)
                payload.update(totals=totals, cases=cases)
            else:
                payload.update(totals={"passed": 0, "failed": 0, "skipped": 0}, cases=[],
                               stage_failure="no JUnit output (runner crashed)")
            executed = sum(payload["totals"].values())
            payload["executed"] = executed
            payload["excluded"] = max(0, suite_total - executed)
            os.unlink(xml_path)
            return self._store(sly_data, run_id, payload)
        except Exception as e:
            return f"Error: {e}"

    def _timeout(self, repo_name) -> int:
        try:
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            return int(((cfg.get("repos") or {}).get(repo_name) or {}).get("test_timeout_seconds", 900))
        except Exception:
            return 900

    def _store(self, sly_data, run_id, payload) -> Dict[str, Any]:
        wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by="test_runner")
        contracts.validate("test_results", wrapped)
        sly_data["test_results"] = wrapped
        logger.info("run %s: test_runner %s totals=%s timed_out=%s", run_id,
                    payload["runner"], payload["totals"], payload["timed_out"])
        return payload

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
