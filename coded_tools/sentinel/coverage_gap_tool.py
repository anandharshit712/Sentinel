"""coverage_gap_tool (09 §4 P3.3) — which changed code no test executes.

Intersects `change_profile.functions_changed` with the coverage map `test_runner` collected, and
ranks what is missing. This is arithmetic over two files, so it is a coded tool, not an agent: an
LLM asked "is this function covered?" can only introduce a way to get it wrong.

Ranking exists because a gap list is useless unsorted — a 200-function diff produces more gaps than
anyone will read. Priority favours, in order: code in a file carrying a sensitive flag (auth,
payments, migrations), brand-new functions, and the amount of untested code.

`measured: false` is a first-class outcome, not an error. A project without coverage tooling, a JS
repo, a timed-out suite — all produce no coverage report, and saying so plainly beats implying that
everything is covered. Nothing downstream may treat an unmeasured run as a clean one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import coverage_parse

logger = logging.getLogger("coded_tools.coverage_gap")

# A function is only "covered" if every executable line the tests could reach did run; anything in
# between is partial, which is where the interesting gaps live (an untested error branch).
_PARTIAL_FLOOR = 0.999


def _priority(status: str, uncovered: int, is_new: bool, sensitive: bool) -> float:
    """Higher first. Sensitive beats new beats size, and a covered function is never a gap."""
    if status == "covered":
        return 0.0
    score = 1.0 if status == "partial" else 2.0     # a wholly untested function outranks a partial
    if sensitive:
        score += 4.0                                 # auth/payments/migration code first
    if is_new:
        score += 2.0                                 # new code has no track record at all
    score += min(uncovered, 20) / 10.0               # size breaks ties, capped so it cannot dominate
    return round(score, 2)


def analyse(profile: Dict[str, Any], cov: coverage_parse.CoverageMap) -> Dict[str, Any]:
    """The whole computation, free of sly_data and the framework — so it is testable directly."""
    sensitive_files = set()
    for sf in profile.get("sensitive_flags", []) or []:
        sensitive_files.update(sf.get("files", []) or [])

    gaps: List[Dict[str, Any]] = []
    measured_lines = covered_lines = 0
    for f in profile.get("files", []) or []:
        path = f.get("path", "")
        if f.get("change_type") == "deleted":
            continue
        for fn in f.get("functions_changed", []) or []:
            start, end = fn.get("line_start"), fn.get("line_end")
            if not start or not end:
                continue
            # The BODY only. coverage.py records the `def` line as executed the moment the module
            # is imported, so counting it would make a function whose body never runs look
            # "partial" instead of "uncovered" — it would hide exactly the gaps this tool exists
            # to find. A one-line def has no body to measure and is skipped below.
            body = range(start + 1, end + 1)
            statuses = [coverage_parse.line_status(cov, path, ln) for ln in body]
            executable = [s for s in statuses if s != "unmeasured"]
            if not executable:
                continue                       # nothing instrumented here: no evidence either way
            hit = sum(1 for s in executable if s == "covered")
            ratio = hit / len(executable)
            status = ("covered" if ratio >= _PARTIAL_FLOOR
                      else "uncovered" if hit == 0 else "partial")
            uncovered = [ln for ln in body
                         if coverage_parse.line_status(cov, path, ln) == "uncovered"]
            measured_lines += len(executable)
            covered_lines += hit
            sensitive = path in sensitive_files
            gaps.append({
                "file": path, "function": fn.get("name", "?"),
                "line_start": start, "line_end": end,
                "status": status, "uncovered_lines": uncovered,
                "covered_lines": hit, "total_lines": len(executable),
                "is_new": bool(fn.get("is_new")), "sensitive": sensitive,
                "priority": _priority(status, len(uncovered), bool(fn.get("is_new")), sensitive),
            })

    gaps.sort(key=lambda g: (-g["priority"], g["file"], g["line_start"]))
    totals = {
        "functions_changed": len(gaps),
        "uncovered": sum(1 for g in gaps if g["status"] == "uncovered"),
        "partial": sum(1 for g in gaps if g["status"] == "partial"),
        "covered": sum(1 for g in gaps if g["status"] == "covered"),
        "changed_lines_measured": measured_lines,
        "changed_lines_covered": covered_lines,
    }
    return {"gaps": gaps, "measured": True, "totals": totals}


class CoverageGapTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            results = sly_data.get("test_results") or {}
            report = args.get("coverage_report") or results.get("coverage_report")
            if not report:
                # Honest empty result. Callers must not read this as "everything is covered".
                return {"gaps": [], "measured": False,
                        "reason": "no coverage report (pytest-cov absent, non-Python repo, or the "
                                  "suite did not finish)"}
            root = sly_data.get("repo_workspace")
            cov = coverage_parse.load(report, root)
            if not cov:
                return {"gaps": [], "measured": False,
                        "reason": f"coverage report at {report} was empty or unparseable"}
            out = analyse(profile, cov)
            logger.info("run %s: coverage_gap %d changed function(s), %d uncovered, %d partial",
                        run_id, out["totals"]["functions_changed"],
                        out["totals"]["uncovered"], out["totals"]["partial"])
            return out
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    profile = {
        "files": [
            {"path": "app/auth.py", "change_type": "modified", "functions_changed": [
                {"name": "login", "kind": "function", "line_start": 1, "line_end": 4, "is_new": False}]},
            {"path": "app/util.py", "change_type": "modified", "functions_changed": [
                {"name": "slug", "kind": "function", "line_start": 1, "line_end": 3, "is_new": True},
                {"name": "done", "kind": "function", "line_start": 5, "line_end": 6, "is_new": False}]},
        ],
        "sensitive_flags": [{"flag": "auth", "files": ["app/auth.py"]}],
    }
    cov = {
        # line 1 is the `def` line in each: coverage.py marks it run at import time, which is why
        # only the body counts. auth.login's body never ran; util.slug's ran in part.
        "app/auth.py": {1: 1, 2: 0, 3: 0, 4: 0},
        "app/util.py": {1: 1, 2: 0, 3: 1, 5: 1, 6: 1},
    }
    out = analyse(profile, cov)
    by_fn = {g["function"]: g for g in out["gaps"]}
    assert by_fn["login"]["status"] == "uncovered", by_fn["login"]
    assert by_fn["slug"]["status"] == "partial", by_fn["slug"]
    assert by_fn["done"]["status"] == "covered", by_fn["done"]

    # ordering: sensitive-file gap first, then the new partial, and a covered function ranks last
    assert [g["function"] for g in out["gaps"]] == ["login", "slug", "done"], out["gaps"]
    assert by_fn["done"]["priority"] == 0.0

    assert by_fn["login"]["uncovered_lines"] == [2, 3, 4]
    assert by_fn["login"]["total_lines"] == 3, "the def line is not part of the body"
    assert out["totals"] == {"functions_changed": 3, "uncovered": 1, "partial": 1, "covered": 1,
                             "changed_lines_measured": 6, "changed_lines_covered": 2}, out["totals"]

    # a function nobody instrumented is not reported as a gap — absence of evidence, not evidence
    unmeasured = analyse({"files": [{"path": "x.py", "change_type": "modified",
                                     "functions_changed": [{"name": "f", "line_start": 1,
                                                            "line_end": 2}]}]}, {})
    assert unmeasured["gaps"] == []

    # no coverage report at all -> measured False, never an empty "all clear"
    res = CoverageGapTool().invoke({}, {"run_id": "d", "change_profile": profile})
    assert res["measured"] is False and res["gaps"] == [] and "reason" in res, res

    print("coverage_gap_tool OK")


if __name__ == "__main__":
    demo()
