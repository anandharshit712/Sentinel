"""P2: performance + compliance review cluster (08). Logic-only, no LLM/DB.

The rule tables have their own `demo()` self-checks; these tests cover what those cannot — the
coded tools' contract shape, the exclusion behaviour, and the four-source merge in
report_publisher, which is the piece that has to keep working when a reviewer drifts.
"""
from coded_tools.sentinel.license_scanner_tool import LicenseScannerTool
from coded_tools.sentinel.performance_scanner_tool import PerformanceScannerTool
from coded_tools.sentinel.report_publisher_tool import _synthesize
from lib import contracts, license_rules, perf_rules


def _profile(files):
    """files: {path: [(line_no, source)]} -> a change_profile with those added lines."""
    out = []
    for path, lines in files.items():
        start = lines[0][0]
        patch = "@@\n" + "".join("+" + src + "\n" for _, src in lines)
        out.append({"path": path, "hunks": [{"new_start": start, "patch": patch}]})
    return {"files": out}


# ---------------------------------------------------------------- rule tables
def test_perf_rules_selfcheck():
    perf_rules.demo()


def test_license_rules_selfcheck():
    license_rules.demo()


def test_loop_context_closes_at_dedent():
    ctx = perf_rules.line_contexts("for a in b:\n    x()\ny()\n")
    assert ctx[1][0] == 1, "inside the loop body"
    assert ctx[2][0] == 0, "dedent closes the loop"


def test_perf_severity_never_critical():
    assert {sev for _, _, sev, _, _, _ in perf_rules.PERF_RULES} <= {"medium", "low"}


def test_compliance_severity_never_critical():
    src = ("log.info('email=%s', u.email)\n"
           "# SPDX-License-Identifier: GPL-3.0-only\n"
           "x = datetime.utcnow()\n")
    sevs = {f["severity"] for f in license_rules.scan_file("a.py", src, [1, 2, 3], "permissive")}
    assert "critical" not in sevs, sevs


# ---------------------------------------------------------------- coded tools
def test_performance_scanner_returns_floor_and_snippets():
    prof = _profile({"app/orders.py": [(10, "    for i in ids:"),
                                       (11, "        rows.append(db.query(i))")]})
    out = PerformanceScannerTool().invoke({}, {"run_id": "t", "change_profile": prof})
    assert [f["category"] for f in out["findings"]] == ["n_plus_one"]
    assert out["findings"][0]["source"] == "tool"
    assert {s["line"] for s in out["added_lines"]} == {10, 11}, "the LLM needs the diff itself"


def test_performance_scanner_honours_exclusions():
    prof = _profile({"node_modules/x/index.js": [(1, "for (const i of ids) { await f(i); }")]})
    out = PerformanceScannerTool().invoke({}, {"run_id": "t", "change_profile": prof})
    assert out["findings"] == [] and out["files_scanned"] == 0


def test_license_scanner_flags_pii_and_reports_its_own_limits():
    prof = _profile({"app/users.py": [(3, "log.info('signup email=%s', user.email)")]})
    out = LicenseScannerTool().invoke({}, {"run_id": "t", "change_profile": prof})
    assert [f["category"] for f in out["findings"]] == ["pii_exposure"]
    # the tool must not imply a dependency audit it did not perform
    assert "not resolve" in out["note"].lower() or "NOT resolved" in out["note"]


def test_license_scanner_flags_a_manifest_relicensing_itself():
    prof = _profile({"package.json": [(4, '  "license": "GPL-3.0-only",')]})
    out = LicenseScannerTool().invoke({}, {"run_id": "t", "change_profile": prof})
    assert [(f["category"], f["severity"]) for f in out["findings"]] == [("license_conflict", "high")]
    assert out["findings"][0]["file"] == "package.json"


# ---------------------------------------------------------------- merge
def _findings(**kw):
    return {"findings": [kw]}


def test_report_merges_all_four_sources():
    sly = {
        "run_id": "t",
        "security_findings": _findings(id="SEC-1", severity="critical", title="SQL injection",
                                       source="tool", file="a.py", line_start=1),
        "quality_findings": _findings(id="QUAL-1", severity="low", title="Long function",
                                      source="llm", file="b.py", line_start=2),
        "performance_findings": _findings(id="PERF-1", severity="medium", title="N+1 query",
                                          source="tool", file="c.py", line_start=3),
        "compliance_findings": _findings(id="COMP-1", severity="high", title="PII in log",
                                         source="tool", file="d.py", line_start=4),
    }
    rep = _synthesize(sly)
    titles = [f["title"] for f in rep["findings"]]
    assert set(titles) == {"SQL injection", "PII in log", "N+1 query", "Long function"}
    assert rep["counts"] == {"critical": 1, "high": 1, "medium": 1, "low": 1}
    assert titles[0] == "SQL injection", "worst finding ranks first"
    assert rep["recommendation"] == "request_changes"
    contracts.validate("review_report", contracts.wrap(rep, run_id="t", produced_by="report_publisher"))


def test_floor_survives_a_reviewer_that_never_ran():
    """The whole point of the deterministic floor: no reviewer contracts, findings anyway."""
    sly = {"run_id": "t", "change_profile": _profile({"app/orders.py": [
        (10, "    for i in ids:"),
        (11, "        rows.append(db.query(i))"),
        (12, "    log.info('email=%s', u.email)"),
    ]})}
    rep = _synthesize(sly)
    cats = {f["category"] for f in rep["findings"]}
    assert "n_plus_one" in cats and "pii_exposure" in cats, rep["findings"]
    assert all(f["source"] == "tool" for f in rep["findings"])


def test_new_contracts_validate():
    for name in ("performance_findings", "compliance_findings"):
        contracts.validate(name, contracts.sample(name, run_id="t"))


def test_contract_store_allows_every_name_the_network_asks_agents_to_write():
    """Regression guard for a silent failure mode.

    A contract name that an agent is instructed to write but `contract_store` does not allow fails
    softly: the agent retries the rejected call dozens of times, gives up, and the run still
    "passes" because the deterministic floor covers for it. Found exactly that way when the Phase 2
    agents were added (167 contract_store calls across 3 runs, 9 writes).
    """
    import re
    from pathlib import Path

    from coded_tools.sentinel.contract_store_tool import _ALLOWED

    hocon = Path(__file__).resolve().parents[2] / "registries" / "sentinel.hocon"
    asked = set(re.findall(r'contract_name\s+"([a-z_0-9]+)"', hocon.read_text(encoding="utf-8")))
    assert asked, "no contract_name instructions found — did the HOCON wording change?"
    missing = asked - _ALLOWED
    assert not missing, f"agents are told to write contracts contract_store rejects: {sorted(missing)}"


def test_summary_never_contradicts_the_finding_list():
    """The senior narrative is security-only; the report is not (08).

    Observed in a live P2 run: an exec summary reading "No high, medium, or low severity findings
    were reported" sat directly above a list containing two highs and a medium.
    """
    sly = {
        "run_id": "t",
        "senior_summary": {"summary": "One critical credential leak; no other security issues."},
        "compliance_findings": _findings(id="COMP-1", severity="high", title="PII in log",
                                         source="tool", file="d.py", line_start=4),
        "performance_findings": _findings(id="PERF-1", severity="medium", title="N+1 query",
                                          source="tool", file="c.py", line_start=3),
    }
    summary = _synthesize(sly)["executive_summary"]
    assert "no other security issues" in summary, "the senior narrative is kept"
    assert "1 high" in summary and "1 medium" in summary, f"counts must follow the prose: {summary}"


def test_contract_store_accepts_a_json_string_payload():
    """Models send `payload` as a JSON string about as often as an object.

    Rejecting the string form cost the entire LLM review layer: the agent retried the identical
    call ~50 times, gave up, and the deterministic floor made the run look fine. Measured at 89
    contract_store calls for 1 write in a single run before the fix.
    """
    import json as _json

    from coded_tools.sentinel.contract_store_tool import ContractStoreTool

    payload = {"findings": [{"id": "P-1", "severity": "medium", "title": "N+1", "source": "llm"}]}
    sly = {"run_id": "00000000-0000-0000-0000-000000000000"}
    tool = ContractStoreTool()

    assert tool.invoke({"contract_name": "performance_findings", "payload": payload}, sly) == \
        {"stored": "performance_findings"}
    assert tool.invoke({"contract_name": "compliance_findings",
                        "payload": _json.dumps(payload)}, sly) == {"stored": "compliance_findings"}
    assert sly["compliance_findings"]["findings"][0]["id"] == "P-1", "the parsed payload is stored"
    # still strict about what it cannot parse
    assert "Error" in tool.invoke({"contract_name": "quality_findings", "payload": "not json"}, sly)


# ---------------------------------------------------------------- deterministic tail (08 §6)
def test_finalize_run_produces_a_decision_from_stored_contracts():
    """The tail must reach a verdict with no LLM turn — that is the point of collapsing it (08 §6)."""
    from coded_tools.sentinel.finalize_run_tool import FinalizeRunTool

    event = {"repo": {"name": "t-repo"}, "target_transition": {"from_env": "qa", "to_env": "staging"}}
    sly = {
        "run_id": "00000000-0000-0000-0000-000000000000",
        "event": event,
        "change_profile": {"files": [], "loc_added": 3, "loc_removed": 0,
                           "sensitive_flags": [], "blast_radius": {"count": 0}},
        "review_report": {"findings": [{"id": "SEC-1", "severity": "critical",
                                        "title": "SQL injection", "source": "tool"}],
                          "counts": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                          "pr_health_score": 40, "recommendation": "request_changes"},
        "test_results": {"totals": {"passed": 1, "failed": 0, "skipped": 0}},
    }
    res = FinalizeRunTool().invoke({}, sly)
    assert isinstance(res, dict), res          # incident_history needs the DB; see conftest

    assert sly["env_context"]["target_env"] == "staging"
    assert sly["risk_score"]["score"] >= 40, "a critical finding must drive the score up"
    assert sly["ladder_verdict"]["decision"] == res["decision"]
    assert res["decision"] != "promote", f"critical on qa->staging must not auto-promote: {res}"


def test_network_no_longer_routes_the_tail_through_llm_agents():
    """Guards the collapse: the three tail agents are gone and finalize_run is on the chain."""
    from pathlib import Path

    hocon = (Path(__file__).resolve().parents[2] / "registries" / "sentinel.hocon").read_text(encoding="utf-8")
    for gone in ("environment_context_agent", "risk_scoring_agent", "promotion_gating_agent"):
        assert gone not in hocon, f"{gone} should have been folded into finalize_run"
    assert '"name": "finalize_run"' in hocon
