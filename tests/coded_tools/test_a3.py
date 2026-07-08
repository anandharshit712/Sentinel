"""A3 complexity_metrics: measured McCabe delta base vs head over a golden diff."""
import subprocess

import pytest

from coded_tools.sentinel.git_diff_tool import GitDiffTool
from coded_tools.sentinel.ast_analyzer_tool import AstAnalyzerTool
from coded_tools.sentinel.complexity_metrics_tool import ComplexityMetricsTool


def _git(r, *a):
    subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)


def _rev(r):
    return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "svc"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "calc.py").write_text("def score(x):\n    return x\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base = _rev(r)
    (r / "calc.py").write_text(
        "def score(x):\n"
        "    if x > 0:\n"
        "        for i in range(x):\n"
        "            if i % 2:\n"
        "                x += i\n"
        "    return x\n\n"
        "def helper(y):\n"
        "    return y and True\n"
    )
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "head")
    return r, base, _rev(r)


def test_complexity_delta_and_new_function(repo):
    r, base, head = repo
    sly = {"run_id": "x"}
    args = {"repo_path": str(r), "base_ref": base, "head_ref": head}
    GitDiffTool().invoke(args, sly)
    AstAnalyzerTool().invoke(args, sly)
    out = ComplexityMetricsTool().invoke(args, sly)
    assert not isinstance(out, str), out
    by_name = {m["name"]: m for m in out["functions"]}
    assert by_name["score"]["complexity_base"] == 1
    assert by_name["score"]["complexity_delta"] >= 3  # +if +for +if
    assert by_name["helper"]["complexity_base"] == 0   # brand new
    assert by_name["helper"]["complexity_head"] >= 2   # 1 + boolop(and)
    assert out["max_delta"] >= 3
