"""P3: coverage parsing + mutation operators (09). Logic-only, no LLM/DB.

The rule modules carry their own `demo()` self-checks; these cover the properties the rest of the
phase will lean on — that a coverage map distinguishes "not run" from "not measured", and that a
mutation campaign is reproducible and actually changes behaviour. Without the second, a mutation
score means nothing.
"""
import ast
import json
import os

from lib import coverage_parse, mutate


# ---------------------------------------------------------------- coverage
def test_coverage_selfcheck():
    coverage_parse.demo()


def test_formats_agree_on_the_same_data():
    """A project may emit any of the three; the gap analysis must not care which."""
    cj = coverage_parse.parse_coverage_json(json.dumps(
        {"files": {"a.py": {"executed_lines": [1], "missing_lines": [2]}}}))
    cx = coverage_parse.parse_cobertura_xml(
        '<coverage><packages><package><classes><class filename="a.py"><lines>'
        '<line number="1" hits="1"/><line number="2" hits="0"/>'
        "</lines></class></classes></package></packages></coverage>")
    cl = coverage_parse.parse_lcov("SF:a.py\nDA:1,1\nDA:2,0\nend_of_record\n")
    assert cj == cx == cl == {"a.py": {1: 1, 2: 0}}


def test_uncovered_is_not_the_same_as_unmeasured():
    """The distinction the whole gap analysis rests on: a line nobody measured is not a gap."""
    cov = coverage_parse.parse_coverage_json(json.dumps(
        {"files": {"a.py": {"executed_lines": [1], "missing_lines": [2]}}}))
    assert coverage_parse.line_status(cov, "a.py", 1) == "covered"
    assert coverage_parse.line_status(cov, "a.py", 2) == "uncovered"
    assert coverage_parse.line_status(cov, "a.py", 3) == "unmeasured"
    assert coverage_parse.line_status(cov, "never_measured.py", 1) == "unmeasured"


def test_a_broken_report_never_breaks_a_finished_run():
    for bad in ("{oops", "<xml", "SF:\nDA:nonsense", ""):
        assert coverage_parse.parse_coverage_json(bad) == {}
        assert coverage_parse.parse_cobertura_xml(bad) == {}
    assert coverage_parse.load("does/not/exist.json") == {}


# ---------------------------------------------------------------- mutation
def test_mutate_selfcheck():
    mutate.demo()


SRC = (
    "def discount(qty, unit, member):\n"
    "    if qty > 10 and member:\n"
    "        return qty * unit * 0.9\n"
    "    return qty * unit\n"
)


def test_every_mutant_is_valid_python_and_differs():
    muts = mutate.generate(SRC, "discount")
    assert muts, "the fixture has comparison, boundary, logic and arithmetic opportunities"
    for m in muts:
        ast.parse(m.source)                     # compiles
        assert m.source != SRC                  # differs
        assert m.description and m.line > 0     # explains itself to a human


def test_campaign_is_reproducible():
    """A mutation score is only comparable across runs if the campaign is identical."""
    a, b = mutate.generate(SRC, "discount"), mutate.generate(SRC, "discount")
    assert [(m.id, m.source) for m in a] == [(m.id, m.source) for m in b]


def test_mutants_actually_change_behaviour():
    """An operator that produces equivalent mutants inflates every score it touches."""
    ns: dict = {}
    exec(compile(SRC, "<o>", "exec"), ns)
    baseline = [ns["discount"](*args) for args in ((11, 10.0, True), (2, 5.0, False), (11, 1.0, False))]
    differing = 0
    for m in mutate.generate(SRC, "discount"):
        mns: dict = {}
        try:
            exec(compile(m.source, "<m>", "exec"), mns)
            got = [mns["discount"](*args) for args in ((11, 10.0, True), (2, 5.0, False), (11, 1.0, False))]
        except Exception:
            differing += 1                      # a crash is detectable
            continue
        if got != baseline:
            differing += 1
    assert differing >= len(mutate.generate(SRC, "discount")) * 0.5, differing


def test_one_edit_per_mutant():
    """Each mutant must isolate a single bug, or a 'caught' verdict says nothing specific.

    Measured per source line: node-level diffs shift everything after an insertion (wrapping a
    return value in `not` adds a node), which says nothing about how localised the edit was.
    """
    base = ast.unparse(ast.parse(SRC)).splitlines()
    for m in mutate.generate(SRC, "discount"):
        got = ast.unparse(ast.parse(m.source)).splitlines()
        assert len(got) == len(base), f"{m.id} changed the line count: {m.description}"
        differing = [i for i, (a, b) in enumerate(zip(base, got)) if a != b]
        assert len(differing) == 1, f"{m.id} touched {len(differing)} lines: {m.description}"


def test_only_the_target_function_is_mutated():
    src = SRC + "\ndef other(a, b):\n    return a > b\n"
    for m in mutate.generate(src, "discount"):
        assert "def other(a, b):\n    return a > b" in m.source.replace("\r", ""), m.description


def test_limit_is_respected():
    assert len(mutate.generate(SRC, "discount", limit=3)) == 3


# ---------------------------------------------------------------- coverage gaps
from coded_tools.sentinel.coverage_gap_tool import CoverageGapTool, analyse  # noqa: E402

from lib import contracts  # noqa: E402


def test_coverage_gap_selfcheck():
    from coded_tools.sentinel import coverage_gap_tool
    coverage_gap_tool.demo()


def test_def_line_does_not_disguise_an_untested_function():
    """coverage.py marks `def` as executed at import.

    Counting it would report every untested function as merely 'partial' — the gaps this tool
    exists to find would be the ones it hides.
    """
    profile = {"files": [{"path": "a.py", "change_type": "modified", "functions_changed": [
        {"name": "f", "line_start": 1, "line_end": 3}]}]}
    out = analyse(profile, {"a.py": {1: 1, 2: 0, 3: 0}})
    assert out["gaps"][0]["status"] == "uncovered", out["gaps"][0]
    assert out["gaps"][0]["total_lines"] == 2


def test_sensitive_code_outranks_a_bigger_ordinary_gap():
    profile = {
        "files": [
            {"path": "app/auth.py", "change_type": "modified", "functions_changed": [
                {"name": "login", "line_start": 1, "line_end": 3}]},
            {"path": "app/util.py", "change_type": "modified", "functions_changed": [
                {"name": "helper", "line_start": 1, "line_end": 12, "is_new": True}]},
        ],
        "sensitive_flags": [{"flag": "auth", "files": ["app/auth.py"]}],
    }
    cov = {"app/auth.py": {1: 1, 2: 0, 3: 0},
           "app/util.py": {n: (1 if n == 1 else 0) for n in range(1, 13)}}
    assert [g["function"] for g in analyse(profile, cov)["gaps"]] == ["login", "helper"]


def test_unmeasured_is_never_reported_as_covered():
    """The failure mode that matters: a run with no coverage data must not look clean."""
    res = CoverageGapTool().invoke({}, {"run_id": "t", "change_profile": {"files": []}})
    assert res["measured"] is False
    assert res["gaps"] == []
    assert "reason" in res and res["reason"]

    res2 = CoverageGapTool().invoke(
        {"coverage_report": "no/such/report.json"}, {"run_id": "t", "change_profile": {"files": []}})
    assert res2["measured"] is False


def test_deleted_files_are_not_gaps():
    profile = {"files": [{"path": "gone.py", "change_type": "deleted", "functions_changed": [
        {"name": "old", "line_start": 1, "line_end": 5}]}]}
    assert analyse(profile, {"gone.py": {2: 0}})["gaps"] == []


def test_coverage_gaps_contract_validates():
    profile = {"files": [{"path": "a.py", "change_type": "modified", "functions_changed": [
        {"name": "f", "line_start": 1, "line_end": 3, "is_new": True}]}]}
    out = analyse(profile, {"a.py": {1: 1, 2: 0, 3: 0}})
    contracts.validate("coverage_gaps", contracts.wrap(out, run_id="t", produced_by="coverage_gap"))
    contracts.validate("coverage_gaps", contracts.sample("coverage_gaps", run_id="t"))


# ---------------------------------------------------------------- evaluator
# NOTE: the module's own demo() runs four full mutation campaigns (~80s). It is the manual
# self-check; the suite uses a two-line function so the same properties cost a few seconds.
from coded_tools.sentinel.test_evaluator_tool import TestEvaluatorTool, is_tautology  # noqa: E402

TINY = "def is_adult(age):\n    return age >= 18\n"


def _ws(tmp_path, src=TINY):
    (tmp_path / "person.py").write_text(src, encoding="utf-8")
    return str(tmp_path)


def test_tautology_detection():
    assert is_tautology("def test_x():\n    assert True\n")
    assert is_tautology("def test_x():\n    assert 1 == 1\n")
    assert is_tautology("def test_x():\n    pass\n"), "no assertion asserts nothing"
    assert not is_tautology("def test_x():\n    assert f(1) == 2\n")
    assert not is_tautology("def test_x():\n    assert result.total == 5\n")


def test_a_test_that_catches_bugs_is_accepted(tmp_path):
    good = ("from person import is_adult\n\n\n"
            "def test_boundaries():\n"
            "    assert is_adult(18) is True\n"
            "    assert is_adult(17) is False\n")
    r = TestEvaluatorTool().invoke(
        {"target_file": "person.py", "function": "is_adult", "test_source": good,
         "repo_workspace": _ws(tmp_path)}, {})
    assert r["verdict"] == "accepted", r
    assert r["mutation_score"] >= 0.5 and r["mutants_total"] > 0, r


def test_a_test_that_catches_nothing_is_rejected_with_evidence(tmp_path):
    """The point of the phase: passing is not the bar, catching injected bugs is."""
    weak = ("from person import is_adult\n\n\n"
            "def test_returns_bool():\n"
            "    assert isinstance(is_adult(40), bool)\n")
    r = TestEvaluatorTool().invoke(
        {"target_file": "person.py", "function": "is_adult", "test_source": weak,
         "repo_workspace": _ws(tmp_path)}, {})
    assert r["verdict"] == "rejected", r
    assert r["missed"], "a rejection must name the bugs that slipped through"
    assert all("description" in m for m in r["missed"])


def test_a_test_failing_on_correct_code_is_rejected_before_any_mutant(tmp_path):
    broken = ("from person import is_adult\n\n\n"
              "def test_wrong():\n"
              "    assert is_adult(20) is False\n")
    r = TestEvaluatorTool().invoke(
        {"target_file": "person.py", "function": "is_adult", "test_source": broken,
         "repo_workspace": _ws(tmp_path)}, {})
    assert r["verdict"] == "rejected" and "unmodified" in r["reason"], r
    assert r["mutants_total"] == 0, "no point mutating when the test is already red"


def test_evaluation_never_writes_to_the_workspace(tmp_path):
    """Mutants are written to a throwaway copy — the run workspace must come back untouched."""
    ws = _ws(tmp_path)
    good = ("from person import is_adult\n\n\n"
            "def test_boundaries():\n"
            "    assert is_adult(18) is True\n"
            "    assert is_adult(17) is False\n")
    TestEvaluatorTool().invoke(
        {"target_file": "person.py", "function": "is_adult", "test_source": good,
         "repo_workspace": ws}, {})
    assert sorted(os.listdir(ws)) == ["person.py"], os.listdir(ws)
    assert (tmp_path / "person.py").read_text(encoding="utf-8") == TINY
