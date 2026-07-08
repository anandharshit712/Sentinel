"""A6 table-driven tests — the highest-value tests in the project (07 §5).

Pins: risk band edges, the Demo-2 arithmetic (=95 critical), caps, raise-only LLM escalation
(negative clamped to 0), the trust-ladder matrix, the production hard floor, and fail-closed
on unknown transitions. Decisions rest on these two tools.
"""
from coded_tools.sentinel.risk_calculator_tool import RiskCalculatorTool
from coded_tools.sentinel.trust_ladder_tool import TrustLadderTool
from lib import contracts


# ---------- helpers ----------
def risk(sly, esc=None):
    args = {"llm_escalation": esc} if esc is not None else {}
    return RiskCalculatorTool().invoke(args, sly)  # mutates sly_data (writes risk_score), as in production


def review(critical=0, high=0, medium=0, low=0, health=100):
    return {"counts": {"critical": critical, "high": high, "medium": medium, "low": low},
            "pr_health_score": health}


def ladder(from_env, to_env, band, score):
    sly = {"risk_score": {"band": band, "score": score},
           "event": {"target_transition": {"from_env": from_env, "to_env": to_env}}}
    return TrustLadderTool().invoke({}, sly)["decision"]


# ---------- risk_calculator: band edges (via raise-only escalation, zero other factors) ----------
def test_band_edges():
    cases = {0: "low", 24: "low", 25: "medium", 49: "medium",
             50: "high", 74: "high", 75: "critical", 100: "critical"}
    for pts, band in cases.items():
        r = risk({"run_id": "r"}, {"points_added": pts, "justification": "edge"})
        assert r["score"] == pts and r["band"] == band, (pts, r["score"], r["band"])


# ---------- risk_calculator: Demo Run 2 arithmetic must equal 95 (critical) ----------
def test_demo2_math_is_95_critical():
    sly = {
        "run_id": "demo2",
        "review_report": review(critical=2, health=100),        # SQLi + hardcoded secret: 40+40, cap 80
        "change_profile": {"sensitive_flags": [{"flag": "auth"}], "blast_radius": {"count": 0},
                           "loc_added": 0, "loc_removed": 0},    # +15
    }
    r = risk(sly)
    assert r["score"] == 95, r["explanation"]
    assert r["band"] == "critical"


# ---------- risk_calculator: per-group cap ----------
def test_security_cap():
    r = risk({"run_id": "r", "review_report": review(critical=3, health=100)})  # 120 raw -> cap 80
    sec = next(c for c in r["contributions"] if c["factor"] == "security.critical")
    assert sec["points"] == 80 and sec["cap_applied"] is True
    assert r["score"] == 80 and r["band"] == "critical"


# ---------- risk_calculator: LLM escalation is raise-only ----------
def test_negative_escalation_clamped():
    sly = {"run_id": "r", "review_report": review(critical=1, health=100)}   # 40
    r = risk(sly, {"points_added": -50, "justification": "should not lower"})
    assert r["llm_escalation"]["points_added"] == 0
    assert r["score"] == 40 and r["band"] == "medium"


def test_positive_escalation_raises():
    sly = {"run_id": "r", "review_report": review(critical=1, health=100)}   # 40
    r = risk(sly, {"points_added": 20, "justification": "anomaly"})
    assert r["score"] == 60 and r["band"] == "high"


# ---------- risk_calculator: no factors, and contract validity ----------
def test_zero_and_contract_valid():
    sly = {"run_id": "r"}
    r = risk(sly)
    assert r["score"] == 0 and r["band"] == "low"
    assert contracts.is_valid("risk_score", sly["risk_score"]), contracts.iter_errors("risk_score", sly["risk_score"])


def test_stage_failure_and_smoke():
    sly = {
        "run_id": "r",
        "test_results": {"totals": {"failed": 0, "passed": 0, "skipped": 0}, "timed_out": True, "cases": []},
    }
    r = risk(sly)
    assert any(c["factor"] == "tests.stage_failure" for c in r["contributions"])
    assert r["score"] == 30


# ---------- trust_ladder: full matrix (01 §7) ----------
def test_ladder_dev_to_test():
    assert ladder("dev", "test", "low", 10) == "promote"
    assert ladder("dev", "test", "medium", 30) == "promote"
    assert ladder("dev", "test", "high", 60) == "promote"
    assert ladder("dev", "test", "critical", 80) == "hold"       # < 90
    assert ladder("dev", "test", "critical", 90) == "escalate"   # >= 90 override
    assert ladder("dev", "test", "critical", 95) == "escalate"


def test_ladder_test_to_qa():
    assert ladder("test", "qa", "low", 10) == "promote"
    assert ladder("test", "qa", "medium", 30) == "hold"
    assert ladder("test", "qa", "high", 60) == "hold"
    assert ladder("test", "qa", "critical", 80) == "escalate"


def test_ladder_qa_to_staging():
    assert ladder("qa", "staging", "low", 10) == "promote"
    assert ladder("qa", "staging", "medium", 30) == "escalate"
    assert ladder("qa", "staging", "high", 60) == "escalate"


def test_ladder_production_hard_floor():
    # every band, even low, escalates into production — policy cannot loosen
    for band, score in [("low", 5), ("medium", 30), ("high", 60), ("critical", 90)]:
        assert ladder("staging", "production", band, score) == "escalate"
    # even a transition not modelled but ending at production
    assert ladder("qa", "production", "low", 5) == "escalate"


def test_ladder_unknown_transition_fail_closed():
    assert ladder("dev", "staging", "low", 5) == "escalate"      # not in policy -> default escalate


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\nA6 OK: {len(fns)} tests passed")
