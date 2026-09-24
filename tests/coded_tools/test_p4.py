"""P4: calibration statistics (10). Pure arithmetic, no DB.

These tests care less about the happy path than about the ways a calibration number can lie:
a rate computed from three runs, a promotion with no recorded outcome counted as clean, or a
report that crashes on a fresh install with no history at all.
"""
from lib import calibration


def test_selfcheck():
    calibration.demo()


def _promotions(band: str, n: int, prefix: str = "r"):
    return [{"run_id": f"{prefix}{i}", "decision": "promote", "band": band} for i in range(n)]


# ---------------------------------------------------------------- honesty about small samples
def test_a_rate_below_the_floor_is_not_reported():
    """Three runs is not a rate. A gate re-tuned on one would be re-tuned on noise."""
    out = calibration.promotion_calibration(
        _promotions("low", 3), [{"run_id": "r0", "outcome_type": "incident"}])
    cell = out["low"]["bad_rate"]
    assert cell["insufficient_data"] is True
    assert cell["rate"] is None
    assert cell["n"] == 3, "the count is still reported — only the rate is withheld"


def test_a_rate_at_the_floor_is_reported():
    out = calibration.promotion_calibration(
        _promotions("low", calibration.MIN_SAMPLE), [{"run_id": "r0", "outcome_type": "reverted"}])
    cell = out["low"]["bad_rate"]
    assert cell["insufficient_data"] is False
    assert cell["rate"] == round(1 / calibration.MIN_SAMPLE, 3)


def test_a_promotion_without_an_outcome_is_unknown_not_clean():
    """Silence is not evidence of success — the failure mode that would make any gate look good."""
    out = calibration.promotion_calibration(_promotions("medium", 6), [])
    assert out["medium"]["bad"] == 0
    assert out["medium"]["unknown"] == 6


def test_the_worst_outcome_wins():
    assert calibration.outcomes_by_run([
        {"run_id": "x", "outcome_type": "clean"},
        {"run_id": "x", "outcome_type": "reverted"},
        {"run_id": "x", "outcome_type": "clean"},
    ]) == {"x": "reverted"}


def test_malformed_outcome_rows_are_skipped_not_crashed():
    assert calibration.outcomes_by_run([{}, {"run_id": None}, {"outcome_type": "incident"}]) == {}


# ---------------------------------------------------------------- the approvals signal
def test_escalations_a_human_approved_are_counted_as_calibration_evidence():
    decisions = [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(6)]
    approvals = ([{"run_id": f"h{i}", "status": "approved"} for i in range(5)]
                 + [{"run_id": "h5", "status": "rejected"}])
    out = calibration.escalation_calibration(decisions, approvals)["high"]
    assert out["gated"] == 6 and out["approved"] == 5 and out["rejected"] == 1
    assert out["approval_rate"]["rate"] == round(5 / 6, 3)


def test_unresolved_approvals_are_excluded_from_the_rate():
    """A pending approval is not a human verdict and must not be counted as either."""
    decisions = [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(8)]
    approvals = ([{"run_id": f"h{i}", "status": "approved"} for i in range(5)]
                 + [{"run_id": f"h{i}", "status": "pending"} for i in range(5, 8)])
    out = calibration.escalation_calibration(decisions, approvals)["high"]
    assert out["unresolved"] == 3
    assert out["approval_rate"]["n"] == 5, "the denominator is resolved approvals, not all gates"
    assert out["approval_rate"]["rate"] == 1.0


def test_holds_count_as_gated_too():
    out = calibration.escalation_calibration(
        [{"run_id": "a", "decision": "hold", "band": "medium"}], [])
    assert out["medium"]["gated"] == 1


# ---------------------------------------------------------------- generated tests
def test_adoption_is_tracked_per_verdict():
    proposals = ([{"id": i, "verdict": "accepted", "status": "adopted"} for i in range(4)]
                 + [{"id": 10, "verdict": "accepted", "status": "discarded"}]
                 + [{"id": 11, "verdict": "rejected", "status": "discarded"}])
    out = calibration.generated_test_calibration(proposals, [])
    assert out["accepted"]["adopted"] == 4 and out["accepted"]["discarded"] == 1
    assert out["accepted"]["adoption_rate"]["rate"] == 0.8
    assert out["rejected"]["adopted"] == 0


def test_a_test_that_later_caught_a_real_bug_is_credited():
    out = calibration.generated_test_calibration(
        [{"id": 7, "verdict": "accepted", "status": "adopted"}],
        [{"run_id": "r", "outcome_type": "generated_test_caught_bug", "payload": {"proposal_id": 7}}])
    assert out["accepted"]["caught_bug"] == 1


# ---------------------------------------------------------------- the whole report
def test_report_is_safe_and_honest_on_a_fresh_install():
    """A new install has no history. It must render, and it must not invent figures."""
    r = calibration.report([], [], [], [])
    assert r["totals"] == {"decisions": 0, "promotions": 0, "gated": 0,
                           "outcomes_recorded": 0, "proposals": 0}
    assert r["promotion_by_band"] == {}
    assert r["escalation_by_band"] == {}
    assert r["generated_tests"] == {}


def test_report_states_in_the_data_that_it_changes_nothing():
    """10 §1: this loop recommends, it never re-tunes the gate. The data says so itself, so a
    consumer cannot present it as an applied change."""
    assert calibration.report([], [], [], [])["applies_changes"] is False


# ---------------------------------------------------------------- recommendations (advice only)
def _report(decisions, outcomes=(), approvals=(), proposals=()):
    return calibration.report(list(decisions), list(outcomes), list(approvals), list(proposals))


def test_a_gate_humans_keep_overriding_is_flagged_as_possibly_too_strict():
    decisions = [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(10)]
    approvals = [{"run_id": f"h{i}", "status": "approved"} for i in range(9)] + \
                [{"run_id": "h9", "status": "rejected"}]
    recs = _report(decisions, approvals=approvals)["recommendations"]
    strict = [r for r in recs if r["kind"] == "possibly_too_strict"]
    assert len(strict) == 1 and strict[0]["band"] == "high"
    assert "9 of 10" in strict[0]["evidence"]
    assert strict[0]["applied"] is False


def test_promotions_that_keep_going_wrong_are_flagged_as_possibly_too_loose():
    decisions = [{"run_id": f"p{i}", "decision": "promote", "band": "medium"} for i in range(10)]
    outcomes = ([{"run_id": f"p{i}", "outcome_type": "reverted"} for i in range(3)]
                + [{"run_id": f"p{i}", "outcome_type": "clean"} for i in range(3, 10)])
    recs = _report(decisions, outcomes=outcomes)["recommendations"]
    loose = [r for r in recs if r["kind"] == "possibly_too_loose"]
    assert len(loose) == 1 and loose[0]["band"] == "medium"
    assert "3 of 10" in loose[0]["evidence"]


def test_thin_data_produces_no_recommendation_at_all():
    """The failure mode that matters: confident advice from four data points."""
    decisions = [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(4)]
    approvals = [{"run_id": f"h{i}", "status": "approved"} for i in range(4)]
    assert _report(decisions, approvals=approvals)["recommendations"] == []


def test_every_recommendation_carries_its_caveat_and_confidence():
    decisions = [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(10)]
    approvals = [{"run_id": f"h{i}", "status": "approved"} for i in range(10)]
    for r in _report(decisions, approvals=approvals)["recommendations"]:
        assert r["caveat"] and r["confidence"] in ("low", "moderate")
        assert "config/risk.yaml" in r["suggestion"], "it must name where a human would change it"


def test_nothing_in_the_loop_writes_configuration():
    """10 §1, asserted rather than promised: the module must not touch config at all."""
    import inspect

    src = inspect.getsource(calibration)
    for forbidden in ("open(", "write_text", "yaml.dump", "Path("):
        assert forbidden not in src, f"calibration must not perform I/O; found {forbidden!r}"
    assert all(r["applied"] is False
               for r in _report([{"run_id": f"h{i}", "decision": "escalate", "band": "high"}
                                 for i in range(10)],
                                approvals=[{"run_id": f"h{i}", "status": "approved"}
                                           for i in range(10)])["recommendations"])
