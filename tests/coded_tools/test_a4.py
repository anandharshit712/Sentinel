"""A4: test_mapper (import-graph + smoke) and test_runner (real pytest) on the sample repo."""
import os

import pytest

from coded_tools.sentinel.test_mapper_tool import TestMapperTool
from coded_tools.sentinel.test_runner_tool import TestRunnerTool
from lib import contracts

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
    assert contracts.is_valid("test_results", sly["test_results"]), \
        contracts.iter_errors("test_results", sly["test_results"])
