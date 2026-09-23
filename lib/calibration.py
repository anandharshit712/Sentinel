"""Calibration statistics: was the gate right? (10 §3 P4.1)

Every other part of Sentinel judges a change on evidence available at the time of the run. This
module reads the only evidence that settles whether a decision was correct — what happened
afterwards — and turns it into rates a person can argue with.

Three sources, two of which the project has been collecting since the hackathon without ever
reading them back:

  decisions   what the gate decided, at what band, on which transition
  approvals   what a human did with an escalation. **This is the richest signal in the database.**
              An escalation a human then approved unchanged is evidence the gate was too strict;
              one they rejected is evidence it was right.
  outcomes    what happened to a promotion afterwards (reverted / incident / clean)

Every figure carries its sample size, and any cell below `MIN_SAMPLE` reports `insufficient_data`
instead of a rate. A "67% failure rate" over three runs is not a rate, and a gate re-tuned on one
would be re-tuned on noise. Pure functions over rows — no DB, no I/O — so the arithmetic is
testable without a database.

    python -m lib.calibration        # self-check
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Below this many observations a cell reports no rate at all. Chosen so a single bad week cannot
# look like a trend: with 5 samples one failure is 20%, which is exactly the kind of number that
# reads as signal and is not.
MIN_SAMPLE = 5

# A promotion is only "clean" once it has survived long enough for a problem to show up. Nothing
# proves a silence is good news, so an unsettled promotion counts as neither good nor bad.
SETTLING_DAYS = 7

BAD_OUTCOMES = ("reverted", "incident")


def _rate(good: int, total: int) -> Dict[str, Any]:
    """A rate that refuses to exist below MIN_SAMPLE, and always shows its working."""
    if total < MIN_SAMPLE:
        return {"n": total, "rate": None, "insufficient_data": True}
    return {"n": total, "rate": round(good / total, 3), "insufficient_data": False}


def outcomes_by_run(outcomes: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """run_id -> worst recorded outcome. A run with both `clean` and `incident` is not clean."""
    worst: Dict[str, str] = {}
    rank = {"clean": 0, "escalation_upheld": 0, "escalation_overridden": 1,
            "reverted": 2, "incident": 2}
    for o in outcomes:
        rid, kind = o.get("run_id"), o.get("outcome_type")
        if not rid or not kind:
            continue
        if rid not in worst or rank.get(kind, 0) > rank.get(worst[rid], 0):
            worst[rid] = kind
    return worst


def promotion_calibration(decisions: Iterable[Dict[str, Any]],
                          outcomes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Per band: how often did a promotion at that band go wrong?

    Only PROMOTED runs count here. A held or escalated run has no outcome to judge — nobody ever
    finds out what would have happened if it had shipped. That is the fundamental limit of this
    data, and the reason P4.4 can never claim a threshold is provably too strict: the gate's
    false positives are unobservable by construction.
    """
    by_run = outcomes_by_run(outcomes)
    bands: Dict[str, Dict[str, int]] = {}
    for d in decisions:
        if d.get("decision") != "promote":
            continue
        band = (d.get("band") or "unknown")
        cell = bands.setdefault(band, {"promoted": 0, "bad": 0, "unknown": 0})
        cell["promoted"] += 1
        outcome = by_run.get(d.get("run_id"))
        if outcome in BAD_OUTCOMES:
            cell["bad"] += 1
        elif outcome is None:
            cell["unknown"] += 1        # no outcome recorded: counted, never guessed at
    return {band: {**counts, "bad_rate": _rate(counts["bad"], counts["promoted"])}
            for band, counts in sorted(bands.items())}


def escalation_calibration(decisions: Iterable[Dict[str, Any]],
                           approvals: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Per band: when the gate escalated, did the human agree?

    A high approval rate means the gate stopped changes people considered fine — the signature of
    a threshold that is too tight. It does NOT mean the gate was wrong: a reviewer approving under
    deadline pressure is not proof the change was safe. The statistic is an argument, not a verdict.
    """
    resolved = {}
    for a in approvals:
        if a.get("status") in ("approved", "rejected") and a.get("run_id"):
            resolved[a["run_id"]] = a["status"]
    bands: Dict[str, Dict[str, int]] = {}
    for d in decisions:
        if d.get("decision") not in ("escalate", "hold"):
            continue
        band = (d.get("band") or "unknown")
        cell = bands.setdefault(band, {"gated": 0, "approved": 0, "rejected": 0, "unresolved": 0})
        cell["gated"] += 1
        status = resolved.get(d.get("run_id"))
        if status == "approved":
            cell["approved"] += 1
        elif status == "rejected":
            cell["rejected"] += 1
        else:
            cell["unresolved"] += 1
    out = {}
    for band, c in sorted(bands.items()):
        decided = c["approved"] + c["rejected"]
        out[band] = {**c, "approval_rate": _rate(c["approved"], decided)}
    return out


def generated_test_calibration(proposals: Iterable[Dict[str, Any]],
                               outcomes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Did the mutation score predict anything real?

    Adoption rate by verdict is the near-term signal (do humans keep the tests the gate accepted?);
    `generated_test_caught_bug` is the one that would actually validate the threshold, and it needs
    months of use before it says anything.
    """
    caught_ids = {o.get("payload", {}).get("proposal_id") for o in outcomes
                  if o.get("outcome_type") == "generated_test_caught_bug"}
    buckets: Dict[str, Dict[str, int]] = {}
    for p in proposals:
        verdict = p.get("verdict") or "unknown"
        c = buckets.setdefault(verdict, {"proposed": 0, "adopted": 0, "discarded": 0, "caught_bug": 0})
        c["proposed"] += 1
        if p.get("status") == "adopted":
            c["adopted"] += 1
        elif p.get("status") == "discarded":
            c["discarded"] += 1
        if p.get("id") in caught_ids:
            c["caught_bug"] += 1
    out = {}
    for verdict, c in sorted(buckets.items()):
        decided = c["adopted"] + c["discarded"]
        out[verdict] = {**c, "adoption_rate": _rate(c["adopted"], decided)}
    return out


def report(decisions: List[Dict[str, Any]], outcomes: List[Dict[str, Any]],
           approvals: List[Dict[str, Any]], proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The whole picture. Safe on empty inputs — a fresh install has no history and must not
    look broken (10 §5)."""
    promo = promotion_calibration(decisions, outcomes)
    esc = escalation_calibration(decisions, approvals)
    tests = generated_test_calibration(proposals, outcomes)
    total_decisions = len(list(decisions))
    return {
        "totals": {
            "decisions": total_decisions,
            "promotions": sum(c["promoted"] for c in promo.values()),
            "gated": sum(c["gated"] for c in esc.values()),
            "outcomes_recorded": len(list(outcomes)),
            "proposals": sum(c["proposed"] for c in tests.values()),
        },
        "promotion_by_band": promo,
        "escalation_by_band": esc,
        "generated_tests": tests,
        "min_sample": MIN_SAMPLE,
        "settling_days": SETTLING_DAYS,
        # Said in the data itself, not only in the UI: nothing here has changed a threshold.
        "applies_changes": False,
    }


def demo() -> None:
    decisions = (
        [{"run_id": f"p{i}", "decision": "promote", "band": "low"} for i in range(10)]
        + [{"run_id": f"m{i}", "decision": "promote", "band": "medium"} for i in range(6)]
        + [{"run_id": f"h{i}", "decision": "escalate", "band": "high"} for i in range(8)]
        + [{"run_id": "c0", "decision": "escalate", "band": "critical"}]
    )
    outcomes = ([{"run_id": "m0", "outcome_type": "incident"},
                 {"run_id": "m1", "outcome_type": "reverted"}]
                + [{"run_id": f"p{i}", "outcome_type": "clean"} for i in range(10)])
    approvals = ([{"run_id": f"h{i}", "status": "approved"} for i in range(7)]
                 + [{"run_id": "h7", "status": "rejected"}]
                 + [{"run_id": "c0", "status": "pending"}])
    proposals = ([{"id": 1, "verdict": "accepted", "status": "adopted"},
                  {"id": 2, "verdict": "accepted", "status": "adopted"},
                  {"id": 3, "verdict": "rejected", "status": "discarded"}])

    r = report(decisions, outcomes, approvals, proposals)

    # promotions at medium went bad twice in six
    assert r["promotion_by_band"]["medium"]["promoted"] == 6
    assert r["promotion_by_band"]["medium"]["bad"] == 2
    assert r["promotion_by_band"]["medium"]["bad_rate"]["rate"] == 0.333
    assert r["promotion_by_band"]["low"]["bad"] == 0

    # the escalation signal: 7 of 8 gated changes were approved unchanged
    assert r["escalation_by_band"]["high"]["gated"] == 8
    assert r["escalation_by_band"]["high"]["approval_rate"]["rate"] == 0.875

    # a single critical escalation is not a rate
    assert r["escalation_by_band"]["critical"]["approval_rate"]["insufficient_data"] is True
    assert r["escalation_by_band"]["critical"]["approval_rate"]["rate"] is None

    # small sample -> no rate, but the counts are still reported
    assert r["generated_tests"]["accepted"]["adopted"] == 2
    assert r["generated_tests"]["accepted"]["adoption_rate"]["insufficient_data"] is True

    # a run with conflicting outcomes takes the worst one
    assert outcomes_by_run([{"run_id": "x", "outcome_type": "clean"},
                            {"run_id": "x", "outcome_type": "incident"}]) == {"x": "incident"}

    # a promotion with no recorded outcome is counted as unknown, never as clean
    lone = promotion_calibration([{"run_id": "z", "decision": "promote", "band": "low"}], [])
    assert lone["low"] == {"promoted": 1, "bad": 0, "unknown": 1,
                           "bad_rate": {"n": 1, "rate": None, "insufficient_data": True}}, lone

    # a fresh install: no history, no crash, no invented figures
    empty = report([], [], [], [])
    assert empty["totals"]["decisions"] == 0
    assert empty["promotion_by_band"] == {} and empty["escalation_by_band"] == {}
    assert empty["applies_changes"] is False

    print("lib/calibration.py OK")


if __name__ == "__main__":
    demo()
