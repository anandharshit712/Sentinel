"""A4: test_mapper (import-graph + smoke) and test_runner (real pytest) on the sample repo."""
import os

import pytest

from coded_tools.sentinel.test_mapper_tool import TestMapperTool
from coded_tools.sentinel.test_runner_tool import TestRunnerTool, _detect_runner, _project_dirs
from lib import contracts


def test_monorepo_detection_auto_scans_at_any_depth(tmp_path):
    """Auto-scan finds Python project dirs anywhere (like ORION), prunes junk, no config needed."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "backend" / "pkg").mkdir()  # sub-package: must NOT be listed separately
    (tmp_path / "backend" / "pkg" / "pyproject.toml").write_text("[project]\nname='y'\n")
    (tmp_path / "services" / "sdk").mkdir(parents=True)  # nested 2 levels deep
    (tmp_path / "services" / "sdk" / "requirements.txt").write_text("pytest\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")  # no py marker -> excluded
    (tmp_path / "node_modules" / "junk").mkdir(parents=True)
    (tmp_path / "node_modules" / "junk" / "setup.py").write_text("")  # pruned, must be ignored
    repo = str(tmp_path)
    assert _detect_runner(repo) == "pytest"
    assert set(_project_dirs(repo, None)) == {"backend", "services/sdk"}  # not backend/pkg, not node_modules
    assert _project_dirs(repo, ["backend"]) == ["backend"]                # config override still wins

SAMPLE = os.path.abspath("samples/python-payments-service")

pytestmark = pytest.mark.skipif(not os.path.isdir(SAMPLE), reason="sample repo missing")


def test_mapper_selects_importers_and_smoke():
    sly = {"run_id": "r", "change_profile": {
        "files": [{"path": "app/auth/login.py", "language": "python", "change_type": "modified"}],
        "blast_radius": {"direct": [], "transitive": [], "count": 0}}}
    out = TestMapperTool().invoke({"repo_path": SAMPLE, "repo_name": "python-payments-service"}, sly)
    assert not isinstance(out, str), out
    tp = sly["test_plan"]  # mapper finalizes the test_plan contract into sly_data
    assert contracts.is_valid("test_plan", tp), contracts.iter_errors("test_plan", tp)
    ids = {s["test_id"]: s for s in tp["selected"]}
    assert ids["tests/test_auth.py"]["mapping_source"] == "import_graph"     # imports app.auth.login
    assert "tests/test_payments.py" in ids                                    # imports app.auth.login too
    assert ids["tests/test_health.py::test_health_ok"]["mapping_source"] == "smoke"
    assert tp["selection_confidence"] == "medium"


def test_runner_executes_pytest_subset_and_parses_results():
    plan = {"selected": [{"test_id": "tests/test_auth.py", "mapping_source": "import_graph", "reason": "x"}],
            "smoke_set": ["tests/test_health.py::test_health_ok"]}
    sly = {"run_id": "r", "test_plan": plan}
    out = TestRunnerTool().invoke({"repo_path": SAMPLE}, sly)
    assert not isinstance(out, str), out
    assert out["runner"] == "pytest"
    assert out["totals"]["passed"] >= 4 and out["totals"]["failed"] == 0  # 3 auth + 1 health
    assert out["timed_out"] is False
    # selection visibility: ran a SUBSET of the 6-test suite, with a denominator
    assert out["selection_mode"] == "subset"
    assert out["suite_total"] == 6
    executed = sum(out["totals"].values())
    assert out["executed"] == executed and out["executed"] < out["suite_total"]
    assert out["excluded"] == out["suite_total"] - executed  # 6 - 4 = 2 skipped by selection
    assert contracts.is_valid("test_results", sly["test_results"]), \
        contracts.iter_errors("test_results", sly["test_results"])


def test_runner_empty_plan_is_labeled_full_suite_fallback():
    """No mapped tests -> run the whole suite, but honestly labeled (never silent 'selection')."""
    sly = {"run_id": "r", "test_plan": {"selected": [], "smoke_set": []}}
    out = TestRunnerTool().invoke({"repo_path": SAMPLE}, sly)
    assert not isinstance(out, str), out
    assert out["selection_mode"] == "full_suite_fallback"
    assert out["suite_total"] == 6 and out["executed"] == 6 and out["excluded"] == 0
