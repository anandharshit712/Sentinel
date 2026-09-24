"""Oracle evidence tiers (11). The tests that matter here are about what the system CLAIMS.

The Phase 3 gap was not a wrong number — the mutation score was right. It was a wrong claim:
"accepted" read as "this test is correct" when all it meant was "this test agrees with the
implementation". These tests pin the claim to its evidence.
"""
import os
import shutil
import subprocess
import tempfile

import pytest

from coded_tools.sentinel.test_evaluator_tool import TestEvaluatorTool, classify
from lib import differential


# ---------------------------------------------------------------- differential oracle
def test_differential_selfcheck():
    differential.demo()


@pytest.fixture
def repo(tmp_path):
    """A git repo where `changed` is altered by the change and `stable` is not."""
    ws = str(tmp_path / "repo")
    os.makedirs(ws)
    subprocess.run(["git", "init", "-q", ws], check=True, capture_output=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", ws, "config", k, v], check=True, capture_output=True)

    def write(content):
        with open(os.path.join(ws, "m.py"), "w", encoding="utf-8") as fh:
            fh.write(content)

    def commit(msg):
        subprocess.run(["git", "-C", ws, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", ws, "commit", "-qm", msg], check=True, capture_output=True)

    write("def changed(x):\n    return x\n\n\ndef stable(x):\n    return x * 2\n")
    commit("base")
    base = subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    write("def changed(x):\n    return x + 1\n\n\ndef stable(x):\n    return x * 2\n\n\n"
          "def fresh(x):\n    return 0\n")
    commit("head")
    return ws, base


def test_behaviour_the_change_altered_is_reported_as_changed(repo):
    ws, base = repo
    ev = differential.compare(ws, base, "m.py", "changed", "tests/t.py",
                              "from m import changed\n\n\ndef test_c():\n    assert changed(1) == 2\n")
    assert ev["result"] == differential.CHANGED
    assert ev["base_passed"] is False


def test_behaviour_that_predates_the_change_is_reported_as_unchanged(repo):
    """The useful half: this test cannot have encoded a bug the change introduced."""
    ws, base = repo
    ev = differential.compare(ws, base, "m.py", "stable", "tests/t.py",
                              "from m import stable\n\n\ndef test_s():\n    assert stable(2) == 4\n")
    assert ev["result"] == differential.UNCHANGED
    assert ev["base_passed"] is True


def test_a_function_that_did_not_exist_is_new_code(repo):
    ws, base = repo
    ev = differential.compare(ws, base, "m.py", "fresh", "tests/t.py",
                              "from m import fresh\n\n\ndef test_f():\n    assert fresh(1) == 0\n")
    assert ev["result"] == differential.NEW_CODE


def test_an_unusable_base_is_unknown_never_guessed(repo):
    ws, _ = repo
    assert differential.compare(ws, "", "m.py", "changed", "t.py", "x")["result"] == differential.UNKNOWN
    assert differential.compare(ws, "0" * 40, "m.py", "changed", "t.py",
                                "x")["result"] == differential.UNKNOWN


def test_the_workspace_is_never_modified(repo):
    ws, base = repo
    before = sorted(os.listdir(ws))
    differential.compare(ws, base, "m.py", "changed", "tests/t.py",
                         "from m import changed\n\n\ndef test_c():\n    assert changed(1) == 2\n")
    assert sorted(os.listdir(ws)) == before
    worktrees = subprocess.run(["git", "-C", ws, "worktree", "list"],
                               capture_output=True, text=True, check=True).stdout
    assert worktrees.count("\n") == 1, "the base worktree must be removed afterwards"


# ---------------------------------------------------------------- the claim
def test_a_test_with_no_independent_witness_is_never_called_verified():
    """The Phase 3 overclaim, pinned: no differential evidence means characterization, full stop."""
    cls, why = classify(True, {"result": differential.UNKNOWN})
    assert cls == "characterization"
    assert "what the code does today" in why


def test_classification_names_its_witness():
    assert classify(True, {"result": differential.UNCHANGED})[0] == "regression_guard"
    assert classify(True, {"result": differential.CHANGED})[0] == "change_documented"
    assert classify(True, {"result": differential.NEW_CODE})[0] == "characterization"
    assert classify(False, {"result": differential.UNCHANGED})[0] == "rejected"


def test_evaluator_reports_evidence_per_tier(tmp_path):
    ws = str(tmp_path / "w")
    os.makedirs(ws)
    with open(os.path.join(ws, "p.py"), "w", encoding="utf-8") as fh:
        fh.write("def is_adult(age):\n    return age >= 18\n")
    good = ("from p import is_adult\n\n\ndef test_b():\n"
            "    assert is_adult(18) is True\n    assert is_adult(17) is False\n")
    r = TestEvaluatorTool().invoke(
        {"target_file": "p.py", "function": "is_adult", "test_source": good,
         "repo_workspace": ws}, {})
    ev = r["oracle_evidence"]
    assert ev["mutation"]["score"] >= 0.5
    # the mutation tier states its own limitation in the payload a reviewer reads
    assert "sensitivity, not correctness" in ev["mutation"]["witness"]
    # no git history here, so there is no second witness — and the claim reflects that
    assert ev["differential"]["result"] == differential.UNKNOWN
    assert r["classification"] == "characterization"


# ---------------------------------------------------------------- specification tier
from lib import intent, spec_check  # noqa: E402

BUGGY = ('def loyalty_discount(total, years):\n'
         '    """Members of 3+ years get 15% off."""\n'
         '    if years >= 3:\n'
         '        return round(total * 0.90, 2)\n'
         '    return total\n')


def test_intent_selfcheck():
    intent.demo()


def test_spec_check_selfcheck():
    spec_check.demo()


def test_the_blind_oracle_never_sees_the_implementation():
    """The mechanism the whole tier depends on. If redaction fails open, the tier is worthless
    while still reporting confidence — so this is a correctness test, not a formatting one."""
    red = intent.redact_body(BUGGY, "loyalty_discount")
    assert "0.90" not in red
    assert "years >= 3" not in red
    assert "15%" in red, "stated intent must survive — it is the thing being compared against"


def test_a_docstring_the_code_contradicts_is_caught_without_a_model():
    found = spec_check.check_function(BUGGY, "loyalty_discount")
    assert len(found) == 1
    assert found[0]["documented_percentages"] == [15.0]
    assert 0.9 in found[0]["code_multiplies_by"]


def test_code_matching_its_docstring_is_silent():
    assert spec_check.check_function(BUGGY.replace("0.90", "0.85"), "loyalty_discount") == []


def test_no_claim_means_no_finding():
    """Silence is the correct answer when nothing was documented — not a guess."""
    assert spec_check.check_function("def f(x):\n    return x * 0.9\n", "f") == []


def test_the_original_gap_case_is_now_disputed(tmp_path):
    """The regression test for this entire document: the §1 case must never be 'accepted' again."""
    (tmp_path / "pricing.py").write_text(BUGGY, encoding="utf-8")
    test = ("from pricing import loyalty_discount\n\n\n"
            "def test_d():\n    assert loyalty_discount(100.0, 3) == 90.0\n")
    r = TestEvaluatorTool().invoke(
        {"target_file": "pricing.py", "function": "loyalty_discount",
         "test_source": test, "repo_workspace": str(tmp_path)}, {})

    assert r["classification"] == "disputed", r["classification"]
    assert r["oracle_evidence"]["specification"]["mismatches"], "the disagreement must be evidenced"
    # the mutation score is still reported — it was never wrong, it was just never sufficient
    assert r["mutation_score"] >= 0.5


def test_a_dispute_outranks_a_perfect_mutation_score():
    cls, why = classify(True, {"result": differential.UNCHANGED},
                        [{"evidence": "docs say 15%, code scales by 0.9"}])
    assert cls == "disputed"
    assert "not safe to adopt" in why


# ---------------------------------------------------------------- it is a finding about the CODE
def test_a_contradicted_docstring_becomes_a_review_finding(tmp_path):
    """11 §5: the reframing. A doc/code contradiction is a defect worth telling every reviewer
    about, whether or not anybody asked for a generated test."""
    from coded_tools.sentinel.report_publisher_tool import _synthesize

    (tmp_path / "pricing.py").write_text(BUGGY, encoding="utf-8")
    sly = {
        "run_id": "t",
        "repo_workspace": str(tmp_path),
        "change_profile": {"files": [{
            "path": "pricing.py", "language": "python", "change_type": "modified",
            "functions_changed": [{"name": "loyalty_discount", "kind": "function",
                                   "line_start": 1, "line_end": 5}]}]},
    }
    report = _synthesize(sly)
    specs = [f for f in report["findings"] if f["category"] == "spec_implementation_mismatch"]
    assert len(specs) == 1, report["findings"]
    assert specs[0]["severity"] == "medium"
    assert specs[0]["source"] == "tool", "deterministic, so it survives a drifting reviewer"
    assert "15" in specs[0]["explanation"] and "0.9" in specs[0]["explanation"]
    assert "stale" in specs[0]["explanation"], "it must not claim which side is wrong"


def test_code_agreeing_with_its_docstring_produces_no_finding(tmp_path):
    from coded_tools.sentinel.report_publisher_tool import _synthesize

    (tmp_path / "pricing.py").write_text(BUGGY.replace("0.90", "0.85"), encoding="utf-8")
    sly = {
        "run_id": "t",
        "repo_workspace": str(tmp_path),
        "change_profile": {"files": [{
            "path": "pricing.py", "language": "python", "change_type": "modified",
            "functions_changed": [{"name": "loyalty_discount", "line_start": 1, "line_end": 5}]}]},
    }
    report = _synthesize(sly)
    assert [f for f in report["findings"] if f["category"] == "spec_implementation_mismatch"] == []
