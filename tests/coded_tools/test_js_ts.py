"""JS/TS static analysis: git_diff -> ast_analyzer -> dependency_graph -> complexity_metrics over
golden diffs (mirrors test_a1.py/test_a3.py's Python fixtures), plus test_mapper/test_runner (real
jest) against the samples/node-catalog-service fixture (mirrors test_a4.py)."""
import os
import subprocess

import pytest

from coded_tools.sentinel.git_diff_tool import GitDiffTool
from coded_tools.sentinel.ast_analyzer_tool import AstAnalyzerTool
from coded_tools.sentinel.complexity_metrics_tool import ComplexityMetricsTool
from coded_tools.sentinel.dependency_graph_tool import DependencyGraphTool
from coded_tools.sentinel.test_mapper_tool import TestMapperTool
from coded_tools.sentinel.test_runner_tool import TestRunnerTool, _detect_runner
from lib import contracts


def _git(r, *a):
    subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)


def _write(repo, rel, content):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)


def _rev(r):
    return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "node-catalog-service"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")

    _write(r, "src/auth/login.js", "function authenticate(user, pw) { return user === 'a'; }\n"
                                    "module.exports = { authenticate };\n")
    _write(r, "src/api.js", "const { authenticate } = require('./auth/login');\n"
                            "function loginRoute(user, pw) { return authenticate(user, pw); }\n")
    _write(r, "src/legacy.js", "function old() { return 1; }\n")
    _write(r, "src/util_old.js", "function helper() { return 2; }\n")
    _write(r, "data.bin", b"\x00\x01\x02BASE\x00")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base = _rev(r)

    _write(r, "src/auth/login.js",
           "function authenticate(user, pw) { return user === 'a' && pw === 'b'; }\n"
           "function checkToken(token) { return Boolean(token); }\n"
           "module.exports = { authenticate, checkToken };\n")
    _write(r, "src/audit.js", "function record(x) { return x; }\n")
    (r / "src/legacy.js").unlink()
    (r / "src/util_old.js").rename(r / "src/util.js")
    _write(r, "data.bin", b"\x00\x01\x02HEAD-CHANGED\x00\x09")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "head")
    head = _rev(r)
    return r, base, head


def _run_pipeline(repo, base, head):
    sly = {"run_id": "test-run"}
    args = {"repo_path": str(repo), "base_ref": base, "head_ref": head,
            "repo_name": "node-catalog-service"}
    assert not isinstance(GitDiffTool().invoke(args, sly), str), "git_diff errored"
    assert not isinstance(AstAnalyzerTool().invoke(args, sly), str), "ast errored"
    out = DependencyGraphTool().invoke(args, sly)
    assert not isinstance(out, str), f"dependency_graph errored: {out}"
    return sly


def test_git_diff_tags_js_language_and_change_types(repo):
    r, base, head = repo
    sly = {"run_id": "x"}
    GitDiffTool().invoke({"repo_path": str(r), "base_ref": base, "head_ref": head}, sly)
    files = {f["path"]: f for f in sly["change_profile_wip"]["files"]}
    assert files["src/auth/login.js"]["language"] == "javascript"
    assert files["src/audit.js"]["change_type"] == "added"
    assert files["src/legacy.js"]["change_type"] == "deleted"
    assert files["src/util.js"]["change_type"] == "renamed"


def test_ast_finds_changed_and_new_js_functions(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    cp = sly["change_profile"]
    assert "src/auth/login.js::checkToken" in cp["new_functions"]
    assert "src/audit.js::record" in cp["new_functions"]
    assert "src/util.js::helper" not in cp["new_functions"]  # rename followed, not counted new
    login = next(f for f in cp["files"] if f["path"] == "src/auth/login.js")
    names = {d["name"]: d for d in login["functions_changed"]}
    assert names["authenticate"]["is_new"] is False
    assert names["checkToken"]["is_new"] is True


def test_dependency_graph_blast_and_sensitive_for_js(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    cp = sly["change_profile"]
    assert "src/api" in cp["blast_radius"]["direct"]  # api.js requires the changed auth/login
    flags = {f["flag"] for f in cp["sensitive_flags"]}
    assert "auth" in flags
    auth_flag = next(f for f in cp["sensitive_flags"] if f["flag"] == "auth")
    assert "src/auth/login.js" in auth_flag["files"]


def test_change_profile_is_a_valid_contract_for_js(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    assert contracts.is_valid("change_profile", sly["change_profile"]), \
        contracts.iter_errors("change_profile", sly["change_profile"])


@pytest.fixture
def calc_repo(tmp_path):
    r = tmp_path / "calc-svc"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "calc.js").write_text("function score(x) { return x; }\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base = _rev(r)
    (r / "calc.js").write_text(
        "function score(x) {\n"
        "  if (x > 0) {\n"
        "    for (let i = 0; i < x; i++) {\n"
        "      if (i % 2) { x += i; }\n"
        "    }\n"
        "  }\n"
        "  return x;\n"
        "}\n\n"
        "function helper(y) {\n"
        "  return y && true;\n"
        "}\n"
    )
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "head")
    return r, base, _rev(r)


def test_complexity_delta_and_new_function_for_js(calc_repo):
    r, base, head = calc_repo
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
    assert by_name["helper"]["complexity_head"] >= 2    # 1 + &&


SAMPLE = os.path.abspath("samples/node-catalog-service")

pytestmark = pytest.mark.skipif(not os.path.isdir(os.path.join(SAMPLE, "node_modules")),
                                reason="sample repo deps not installed (npm install)")


def test_mapper_selects_js_importers_and_smoke():
    sly = {"run_id": "r", "change_profile": {
        "files": [{"path": "src/auth/login.js", "language": "javascript", "change_type": "modified"}],
        "blast_radius": {"direct": [], "transitive": [], "count": 0}}}
    out = TestMapperTool().invoke({"repo_path": SAMPLE, "repo_name": "node-catalog-service"}, sly)
    assert not isinstance(out, str), out
    tp = sly["test_plan"]
    assert contracts.is_valid("test_plan", tp), contracts.iter_errors("test_plan", tp)
    ids = {s["test_id"]: s for s in tp["selected"]}
    assert ids["test/auth.test.js"]["mapping_source"] == "import_graph"
    assert ids["test/catalog.test.js"]["mapping_source"] == "import_graph"  # requires auth/login too
    assert ids["test/health.test.js"]["mapping_source"] == "smoke"


def test_runner_executes_jest_subset_and_parses_results():
    assert _detect_runner(SAMPLE) == "jest"
    plan = {"selected": [{"test_id": "test/auth.test.js", "mapping_source": "import_graph", "reason": "x"}],
            "smoke_set": ["test/health.test.js"]}
    sly = {"run_id": "r", "test_plan": plan}
    out = TestRunnerTool().invoke({"repo_path": SAMPLE}, sly)
    assert not isinstance(out, str), out
    assert out["runner"] == "jest"
    assert out["totals"]["passed"] >= 4 and out["totals"]["failed"] == 0  # 3 auth + 1 health
    assert out["timed_out"] is False
    assert out["selection_mode"] == "subset"
    assert out["suite_total"] == 3  # 3 test files total
    executed = sum(out["totals"].values())
    assert out["executed"] == executed and out["executed"] < 7  # fewer than the full 7-test suite
    assert contracts.is_valid("test_results", sly["test_results"]), \
        contracts.iter_errors("test_results", sly["test_results"])


def test_runner_empty_plan_is_labeled_full_suite_fallback_for_jest():
    sly = {"run_id": "r", "test_plan": {"selected": [], "smoke_set": []}}
    out = TestRunnerTool().invoke({"repo_path": SAMPLE}, sly)
    assert not isinstance(out, str), out
    assert out["selection_mode"] == "full_suite_fallback"
    assert out["suite_total"] == 3 and out["executed"] == 7 and out["excluded"] == 0
