"""P3: coverage parsing + mutation operators (09). Logic-only, no LLM/DB.

The rule modules carry their own `demo()` self-checks; these cover the properties the rest of the
phase will lean on — that a coverage map distinguishes "not run" from "not measured", and that a
mutation campaign is reproducible and actually changes behaviour. Without the second, a mutation
score means nothing.
"""
import ast
import json

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
