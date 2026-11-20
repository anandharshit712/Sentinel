"""P6: the rule that decides when CI writes a test (lib/untested) + the JS/TS plumbing around it.

Logic-only: no LLM, no DB, no Node install. Each condition of the rule is tested on its own, in
both languages, so a regression in one cannot hide behind another.
"""
import os
import shutil
import tempfile

import pytest

from lib import coverage_parse, intent, jsexec, untested


@pytest.fixture()
def ws():
    d = tempfile.mkdtemp(prefix="p6-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _w(ws, rel, body):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)


def _fn(path, name, start=1, end=3, **kw):
    return {"path": path, "change_type": kw.pop("change_type", "modified"),
            "functions_changed": [{"name": name, "kind": kw.pop("kind", "function"),
                                   "line_start": start, "line_end": end, **kw}]}


PY = "def price(q):\n    return q * 2\n"
JS = "function price(q) {\n  return q * 2;\n}\nmodule.exports = { price };\n"


def _names(out):
    return [c["function"] for c in out["candidates"]]


def test_selfchecks():
    untested.demo()
    jsexec.demo()


# ---------------------------------------------------------------- no test at all -> candidate
@pytest.mark.parametrize("path,src", [("shop.py", PY), ("src/shop.js", JS)])
def test_untested_function_is_a_candidate(ws, path, src):
    _w(ws, path, src)
    out = untested.candidates({"files": [_fn(path, "price")]}, {"measured": True, "gaps": []}, ws)
    assert _names(out) == ["price"]
    c = out["candidates"][0]
    assert c["status"] == "unmeasured" and c["line_start"] == 1 and c["file"] == path
    assert c["language"] == ("python" if path.endswith(".py") else "javascript")


# ---------------------------------------------------------------- (a) coverage
@pytest.mark.parametrize("status,expected", [("covered", []), ("partial", []),
                                             ("uncovered", ["price"])])
@pytest.mark.parametrize("path,src", [("shop.py", PY), ("src/shop.js", JS)])
def test_a_any_executed_body_line_excludes(ws, path, src, status, expected):
    _w(ws, path, src)
    gaps = {"measured": True, "gaps": [{"file": path, "function": "price", "status": status,
                                        "uncovered_lines": [2], "priority": 3.0}]}
    out = untested.candidates({"files": [_fn(path, "price")]}, gaps, ws)
    assert _names(out) == expected
    if not expected:
        assert "coverage" in out["skipped"][0]["reason"]
    else:
        assert out["candidates"][0]["status"] == "uncovered"
        assert out["candidates"][0]["uncovered_lines"] == [2]


def test_a_def_line_alone_does_not_count_as_covered(ws):
    """The def line runs at import; coverage_gap measures the body, so this is 'uncovered'."""
    from coded_tools.sentinel.coverage_gap_tool import analyse
    profile = {"files": [_fn("shop.py", "price", 1, 2)]}
    gaps = analyse(profile, {"shop.py": {1: 1, 2: 0}})
    assert gaps["gaps"][0]["status"] == "uncovered"
    assert _names(untested.candidates(profile, gaps, tempfile.gettempdir())) == ["price"]


# ---------------------------------------------------------------- (b) an existing test
def test_b_python_import_and_reference_excludes(ws):
    _w(ws, "app/shop.py", PY)
    _w(ws, "tests/test_shop.py", "from app.shop import price\n\n\ndef test_p():\n    assert price(1) == 2\n")
    out = untested.candidates({"files": [_fn("app/shop.py", "price")]}, {}, ws)
    assert _names(out) == [] and "existing test" in out["skipped"][0]["reason"]


def test_b_python_src_layout_and_module_import(ws):
    _w(ws, "src/pkg/shop.py", PY)
    _w(ws, "tests/test_shop.py", "import pkg.shop as s\n\n\ndef test_p():\n    assert s.price(1) == 2\n")
    assert _names(untested.candidates({"files": [_fn("src/pkg/shop.py", "price")]}, {}, ws)) == []


@pytest.mark.parametrize("test_src", [
    # imports the module but never references the function
    "from shop import other\n\n\ndef test_o():\n    assert other() == 1\n",
    # references the name, but from a different module
    "from billing import price\n\n\ndef test_p():\n    assert price(1) == 2\n",
    # mentions it only in a comment and a string
    "from shop import other\n# price is tested elsewhere\n\n\ndef test_o():\n    assert 'price' != other()\n",
])
def test_b_python_needs_both_import_and_reference(ws, test_src):
    _w(ws, "shop.py", PY)
    _w(ws, "tests/test_shop.py", test_src)
    assert _names(untested.candidates({"files": [_fn("shop.py", "price")]}, {}, ws)) == ["price"]


def test_b_js_require_and_reference_excludes(ws):
    _w(ws, "src/shop.js", JS)
    _w(ws, "test/shop.test.js", "const { price } = require('../src/shop');\n"
                                "test('p', () => expect(price(1)).toBe(2));\n")
    out = untested.candidates({"files": [_fn("src/shop.js", "price")]}, {}, ws)
    assert _names(out) == [] and "test/shop.test.js" in out["skipped"][0]["reason"]


def test_b_js_esm_import_and_spec_name(ws):
    _w(ws, "src/shop.ts", JS)
    _w(ws, "src/__tests__/shop.spec.ts", "import { price } from '../shop';\n"
                                         "it('p', () => { expect(price(1)).toBe(2); });\n")
    assert _names(untested.candidates({"files": [_fn("src/shop.ts", "price")]}, {}, ws)) == []


@pytest.mark.parametrize("test_src", [
    "const { other } = require('../src/shop');\ntest('o', () => expect(other()).toBe(1));\n",
    "const { price } = require('../src/billing');\ntest('p', () => expect(price(1)).toBe(2));\n",
    "const { other } = require('../src/shop');\n// price: covered elsewhere\n"
    "test('o', () => expect('price').toBe(other()));\n",
])
def test_b_js_needs_both_import_and_identifier(ws, test_src):
    _w(ws, "src/shop.js", JS)
    _w(ws, "test/shop.test.js", test_src)
    assert _names(untested.candidates({"files": [_fn("src/shop.js", "price")]}, {}, ws)) == ["price"]


# ---------------------------------------------------------------- (c) the PR adds a test
@pytest.mark.parametrize("path,src,test_rel,test_src", [
    ("shop.py", PY, "tests/test_new.py",
     # no import resolvable to shop.py: (b) would not fire, only (c) can
     "from helpers import load\n\n\ndef test_p():\n    assert load().price(1) == 2\n"),
    ("src/shop.js", JS, "test/new.test.js",
     "const m = require('shop-alias');\ntest('p', () => expect(m.price(1)).toBe(2));\n"),
])
def test_c_pr_added_test_excludes(ws, path, src, test_rel, test_src):
    _w(ws, path, src)
    _w(ws, test_rel, test_src)
    profile = {"files": [_fn(path, "price"), {"path": test_rel, "change_type": "added",
                                              "functions_changed": []}]}
    out = untested.candidates(profile, {}, ws)
    assert _names(out) == [] and "this PR" in out["skipped"][0]["reason"], out
    # the same test file NOT in the PR is not enough on its own (no resolvable import)
    profile["files"].pop()
    assert _names(untested.candidates(profile, {}, ws)) == ["price"]


def test_c_pr_test_for_another_function_does_not_exclude(ws):
    _w(ws, "shop.py", PY)
    _w(ws, "tests/test_new.py", "from shop import other\n\n\ndef test_o():\n    assert other()\n")
    profile = {"files": [_fn("shop.py", "price"),
                         {"path": "tests/test_new.py", "change_type": "added"}]}
    assert _names(untested.candidates(profile, {}, ws)) == ["price"]


# ---------------------------------------------------------------- scope + cap
def test_only_changed_functions_in_source_files(ws):
    profile = {"files": [
        _fn("shop.py", "Shop", kind="class"),                       # a class is not a function
        _fn("old.py", "gone", change_type="deleted"),
        _fn("tests/test_x.py", "test_helper"),                     # tests are not targets
        _fn("README.md", "x"),
        _fn("shop.py", "Shop.total", 4, 6, kind="method"),
    ]}
    _w(ws, "shop.py", PY)
    assert _names(untested.candidates(profile, {}, ws)) == ["Shop.total"]


def test_cap_keeps_the_highest_priority(ws):
    files = [_fn(f"m{i}.py", f"f{i}", is_new=(i == 4)) for i in range(5)]
    files.append({"path": "auth.py", "change_type": "modified", "functions_changed": [
        {"name": "login", "kind": "function", "line_start": 1, "line_end": 3}]})
    profile = {"files": files, "sensitive_flags": [{"flag": "auth", "files": ["auth.py"]}]}
    out = untested.candidates(profile, {}, ws, cap=3)
    assert len(out["candidates"]) == 3 and out["truncated"] == 3
    assert _names(out)[:2] == ["login", "f4"], "sensitive first, then new code"
    assert untested.candidates(profile, {}, ws, cap=0)["candidates"] == []


def test_cap_from_repo_config(ws):
    cfg = os.path.join(ws, "repo_config.yaml")
    _w(ws, "repo_config.yaml", "defaults:\n  max_generated_per_pr: 2\nrepos:\n  r1:\n"
                               "    max_generated_per_pr: 5\n")
    assert untested.cap_for("r1", cfg) == 5
    assert untested.cap_for("other", cfg) == 2
    assert untested.cap_for(None, os.path.join(ws, "missing.yaml")) == untested.DEFAULT_CAP
    assert untested.cap_for("node-catalog-service") == 3, "the shipped default"


# ---------------------------------------------------------------- the glue
def test_zero_candidates_means_zero_model_calls(ws):
    _w(ws, "shop.py", PY)
    _w(ws, "tests/test_shop.py", "from shop import price\n\n\ndef test_p():\n    assert price(1) == 2\n")
    sel = untested.candidates_for_run({"change_profile": {"files": [_fn("shop.py", "price")]},
                                       "coverage_gaps": {"measured": True, "gaps": []}}, ws)
    calls = []
    out = untested.generate(sel, calls.append)
    assert sel["candidates"] == [] and calls == [] and out["attempted"] == 0


def test_generate_passes_one_gap_per_call_and_retries_once():
    sel = {"measured": True, "candidates": [{"file": "a.py", "function": "f"},
                                            {"file": "b.py", "function": "g"}]}
    seen, fails = [], {"a.py": 1}

    def invoke(extra):
        gap = extra["coverage_gaps"]["gaps"]
        assert len(gap) == 1 and extra["coverage_gaps"]["measured"] is True
        seen.append(gap[0]["file"])
        if fails.get(gap[0]["file"]):
            fails[gap[0]["file"]] -= 1
            raise RuntimeError("HTTP 500")

    out = untested.generate(sel, invoke)
    assert seen == ["a.py", "a.py", "b.py"] and all(r["error"] is None for r in out["results"])


def test_candidates_for_run_never_raises():
    out = untested.candidates_for_run({"change_profile": {"files": "not-a-list"}}, "/nope")
    assert out["candidates"] == []


# ---------------------------------------------------------------- JS/TS plumbing
def test_gap_context_reads_a_candidate_like_a_gap(ws):
    from coded_tools.sentinel.gap_context_tool import GapContextTool
    _w(ws, "src/shop.js", JS)
    _w(ws, "test/other.test.js", "test('x', () => expect(1).toBe(1));\n")
    sel = untested.candidates({"files": [_fn("src/shop.js", "price")]}, {}, ws)
    out = GapContextTool().invoke({}, {"run_id": "t", "repo_workspace": ws,
                                       "coverage_gaps": {"measured": False,
                                                         "gaps": sel["candidates"]}})
    assert out["gap"]["function"] == "price" and out["framework"] == "jest", out
    assert out["module_path"] == "../src/shop" and out["test_path"] == "test/sentinel.generated.test.js"


def test_lcov_from_jest_in_the_sandbox_maps_to_repo_paths():
    lcov = "SF:/work/src/shop.js\nDA:1,1\nDA:2,0\nend_of_record\n"
    cov = coverage_parse.parse_lcov(lcov, root="C:/host/ws")
    assert coverage_parse.line_status(cov, "src/shop.js", 2) == "uncovered"


def test_stryker_report_counts():
    s = jsexec.summarise_report({"files": {"a.js": {"mutants": [
        {"id": "1", "status": "Killed"}, {"id": "2", "status": "Timeout"},
        {"id": "3", "status": "Survived"}, {"id": "4", "status": "NoCoverage"},
        {"id": "5", "status": "CompileError"}, {"id": "6", "status": "Ignored"}]}}})
    assert (len(s["caught"]), len(s["missed"]), s["total"]) == (2, 2, 4)


def test_js_evaluation_rejects_assertion_free_test_without_running_it(ws):
    from lib.evaluate import evaluate
    _w(ws, "src/shop.js", JS)
    r = evaluate(ws, "src/shop.js", "price", "test('x', () => { require('../src/shop'); });\n")
    assert r["verdict"] == "rejected" and "tautolog" in r["reason"] and r["mutants_total"] == 0
    assert r["test_path"].endswith("sentinel.generated.test.js")


def test_js_intent_hides_the_body():
    src = ("/**\n * Doubles the quantity for the bulk tier, per the 2x promotion rule.\n */\n"
           "function price(q) {\n  return q * 2;\n}\n")
    ctx = intent.collect_js(src, "price", "src/shop.js")
    assert "q * 2" not in ctx["module_without_body"] and "2x promotion" in ctx["docstring"]
