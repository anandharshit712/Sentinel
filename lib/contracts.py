"""Sentinel data contracts (04 §4) — the frozen integration interface.

Single source of truth for the inter-agent contracts (11 schemas / 16 keys). Every track validates and
builds against these schemas + fixtures rather than against each other's code (07 §1.1).
Contract changes start in 01 then propagate (repo rule 1).

Public API:
    validate(name, instance)      -> None  (raises jsonschema.ValidationError)
    is_valid(name, instance)      -> bool
    iter_errors(name, instance)   -> list[str]
    wrap(payload, run_id, produced_by, produced_at=None) -> dict  (stamps envelope)
    sample(name, run_id=...)      -> a valid instance (fixture factory)
    CONTRACTS                     -> {name: json_schema}
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "1"

# ---- shared enums (04 §4) ----
SEVERITY = ["critical", "high", "medium", "low"]
FROM_ENVS = ["dev", "test", "qa", "staging"]
TO_ENVS = ["test", "qa", "staging", "production"]
DECISIONS = ["promote", "hold", "escalate"]
BANDS = ["low", "medium", "high", "critical"]

_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": "number"}
_B = {"type": "boolean"}
_ARR_S = {"type": "array", "items": {"type": "string"}}


# ---- envelope shared by every contract ----
_ENVELOPE_PROPS = {
    "schema_version": _S,
    "run_id": _S,
    "produced_by": _S,
    "produced_at": _S,  # iso8601
}
_ENVELOPE_REQUIRED = ["schema_version", "run_id", "produced_by", "produced_at"]


def _contract(payload_props: dict, payload_required: list[str]) -> dict:
    """Build a full contract schema = envelope + payload."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {**_ENVELOPE_PROPS, **payload_props},
        "required": _ENVELOPE_REQUIRED + payload_required,
    }


# ---- 4.1 event ----
_EVENT = _contract(
    {
        "event_id": _S,
        "source": {"enum": ["github", "manual"]},
        "repo": {
            "type": "object",
            "properties": {"url": _S, "name": _S, "default_branch": _S},
            "required": ["url", "name", "default_branch"],
        },
        "change": {
            "type": "object",
            "properties": {
                "base_sha": _S, "head_sha": _S, "branch": _S, "pr_id": _S,
                "title": _S, "description": _S, "author": _S,
            },
            "required": ["base_sha", "head_sha", "branch", "title", "author"],
        },
        "target_transition": {
            "type": "object",
            "properties": {"from_env": {"enum": FROM_ENVS}, "to_env": {"enum": TO_ENVS}},
            "required": ["from_env", "to_env"],
        },
        "requested_by": _S,
        "received_at": _S,
    },
    ["event_id", "source", "repo", "change", "target_transition", "requested_by"],
)

# ---- 4.2 change_profile ----
_CHANGE_PROFILE = _contract(
    {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": _S,
                    "language": {"enum": ["python", "javascript", "typescript", "other"]},
                    "change_type": {"enum": ["added", "modified", "deleted", "renamed"]},
                    "hunks": {"type": "array"},
                    "functions_changed": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": _S,
                                "kind": {"enum": ["function", "method", "class"]},
                                "line_start": _I, "line_end": _I, "is_new": _B,
                            },
                            "required": ["name", "kind"],
                        },
                    },
                },
                "required": ["path", "language", "change_type"],
            },
        },
        "new_functions": _ARR_S,
        "classification": {"enum": ["feature", "bug_fix", "refactor", "config", "docs", "mixed"]},
        "loc_added": _I,
        "loc_removed": _I,
        "blast_radius": {
            "type": "object",
            "properties": {"direct": _ARR_S, "transitive": _ARR_S, "count": _I},
            "required": ["count"],
        },
        "sensitive_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag": {"enum": ["auth", "payments", "data_deletion", "migration", "public_api"]},
                    "matched_by": _S,
                    "files": _ARR_S,
                },
                "required": ["flag"],
            },
        },
    },
    ["files", "classification", "loc_added", "loc_removed", "blast_radius"],
)

# ---- 4.3 security_findings / quality_findings ----
_FINDING = {
    "type": "object",
    "properties": {
        "id": _S,
        "category": _S,
        "severity": {"enum": SEVERITY},
        "file": _S,
        "line_start": _I,
        "line_end": _I,
        "cwe": _S,
        "title": _S,
        "explanation": _S,
        "fix_suggestion": _S,
        "source": {"enum": ["tool", "llm"]},
    },
    "required": ["id", "severity", "title", "source"],
}
_FINDINGS = _contract(
    {
        "findings": {"type": "array", "items": _FINDING},
        "quality_score": {"type": "integer", "minimum": 0, "maximum": 100},  # quality_findings only
    },
    ["findings"],
)

# ---- 4.4 review_report ----
_REVIEW_REPORT = _contract(
    {
        "executive_summary": _S,
        "findings": {"type": "array", "items": _FINDING},
        "pr_health_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommendation": {"enum": ["approve", "approve_with_changes", "request_changes"]},
        "counts": {
            "type": "object",
            "properties": {"critical": _I, "high": _I, "medium": _I, "low": _I},
        },
        # Present only in audit / fan-out runs (a review_plan existed). Honest reporting of what the
        # LLM deep-reviewed vs what the deterministic rules scanned, so audit output never over-claims.
        "coverage": {
            "type": "object",
            "properties": {
                "total_added_lines": _I,
                "llm_reviewed_lines": _I,
                "deterministic_coverage_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                "shards": _I,
                "unscanned_shards": {"type": "array", "items": _I},
            },
        },
    },
    ["executive_summary", "findings", "pr_health_score", "recommendation"],
)

# ---- 4.10 review_plan (written by review_planner coded tool, not contract_store) ----
_REVIEW_PLAN = _contract(
    {
        "mode": {"enum": ["pr", "audit"]},
        "budget_lines": _I,
        "shards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"shard": _I, "label": _S, "files": _ARR_S, "hotspot_weight": _N},
                "required": ["shard", "files"],
            },
        },
        "metrics": {
            "type": "object",
            "properties": {
                "files_scanned": _I, "excluded_files": _I, "added_lines": _I,
                "hotspot_lines": _I, "shard_count": _I, "basis": _S,
            },
            "required": ["shard_count"],
        },
    },
    ["mode", "shards", "metrics"],
)

# ---- senior_summary (executive narrative from senior_security_agent) ----
_SENIOR_SUMMARY = _contract({"summary": _S}, ["summary"])

# ---- 4.5 test_plan ----
_TEST_PLAN = _contract(
    {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test_id": _S,
                    "reason": _S,
                    "mapping_source": {
                        "enum": ["coverage_map", "import_graph", "convention", "llm_added", "smoke"]
                    },
                },
                "required": ["test_id", "mapping_source"],
            },
        },
        "smoke_set": _ARR_S,
        "excluded_summary": _S,
        "selection_confidence": {"enum": ["high", "medium", "low"]},
        "estimated_runtime_seconds": _I,
    },
    ["selected", "smoke_set", "selection_confidence"],
)

# ---- 4.6 test_results ----
_TEST_RESULTS = _contract(
    {
        "runner": {"enum": ["pytest", "jest", "npm", "none_detected"]},
        "command": _S,
        "totals": {
            "type": "object",
            "properties": {"passed": _I, "failed": _I, "skipped": _I, "errors": _I},
            "required": ["passed", "failed", "skipped"],
        },
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test_id": _S,
                    "status": {"enum": ["passed", "failed", "skipped", "error"]},
                    "duration_ms": _I,
                    "failure_message": _S,
                    "stack": _S,
                },
                "required": ["test_id", "status"],
            },
        },
        "coverage_delta": {
            "type": "object",
            "properties": {"line_pct_before": _N, "line_pct_after": _N},
        },
        "duration_seconds": _N,
        "timed_out": _B,
        "stage_failure": _S,
    },
    ["runner", "command", "totals", "cases", "duration_seconds", "timed_out"],
)

# ---- 4.7 env_context ----
_ENV_CONTEXT = _contract(
    {
        "target_env": _S,
        "incidents": {
            "type": "object",
            "properties": {"count_7d": _I, "count_30d": _I, "most_recent_at": _S},
            "required": ["count_7d", "count_30d"],
        },
        "deploy_window": {
            "type": "object",
            "properties": {"risky": _B, "reason": _S},
            "required": ["risky"],
        },
        "env_stability": {"enum": ["stable", "degraded", "unstable"]},
        "batch_size_commits": _I,
        "flags": _ARR_S,
        "summary": _S,
    },
    ["target_env", "incidents", "deploy_window", "env_stability"],
)

# ---- 4.8 risk_score ----
_RISK_SCORE = _contract(
    {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "band": {"enum": BANDS},
        "formula_version": _S,
        "contributions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor": _S,
                    "points": _N,
                    "cap_applied": _B,
                    "evidence_ref": _S,
                },
                "required": ["factor", "points"],
            },
        },
        "llm_escalation": {
            "type": "object",
            "properties": {"points_added": {"type": "integer", "minimum": 0}, "justification": _S},
            "required": ["points_added"],
        },
        "explanation": _S,
    },
    ["score", "band", "formula_version", "contributions", "explanation"],
)

# ---- 4.9 decision ----
_DECISION = _contract(
    {
        "decision": {"enum": DECISIONS},
        "transition": {
            "type": "object",
            "properties": {"from_env": _S, "to_env": _S},
            "required": ["from_env", "to_env"],
        },
        "policy_version": _S,
        "rule_fired": _S,
        "reasoning_trail": {
            "type": "object",
            "properties": {"review": _S, "testing": _S, "results": _S, "context": _S, "policy": _S},
        },
        "actions_taken": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"enum": ["cicd_promote", "notify", "queue_escalation", "none"]},
                    "detail": _S,
                    "at": _S,
                },
                "required": ["action"],
            },
        },
        "approval_required": _B,
        "approval_status": {"enum": ["pending", "approved", "rejected", "n/a"]},
    },
    ["decision", "transition", "policy_version", "reasoning_trail", "approval_required"],
)


CONTRACTS: dict[str, dict] = {
    "event": _EVENT,
    "change_profile": _CHANGE_PROFILE,
    "security_findings": _FINDINGS,   # 4.3 — same schema, several sly_data keys
    "quality_findings": _FINDINGS,
    # adaptive security fan-out: one findings contract per shard reviewer (all _FINDINGS aliases)
    "security_findings_shard_1": _FINDINGS,
    "security_findings_shard_2": _FINDINGS,
    "security_findings_shard_3": _FINDINGS,
    "security_findings_shard_4": _FINDINGS,
    "review_plan": _REVIEW_PLAN,
    "senior_summary": _SENIOR_SUMMARY,
    "review_report": _REVIEW_REPORT,
    "test_plan": _TEST_PLAN,
    "test_results": _TEST_RESULTS,
    "env_context": _ENV_CONTEXT,
    "risk_score": _RISK_SCORE,
    "decision": _DECISION,
}

_VALIDATORS: dict[str, Draft202012Validator] = {
    name: Draft202012Validator(schema) for name, schema in CONTRACTS.items()
}


def _require_known(name: str) -> None:
    if name not in CONTRACTS:
        raise KeyError(f"unknown contract '{name}'; known: {sorted(CONTRACTS)}")


def validate(name: str, instance: dict) -> None:
    """Raise jsonschema.ValidationError if `instance` is not a valid `name` contract."""
    _require_known(name)
    _VALIDATORS[name].validate(instance)


def is_valid(name: str, instance: dict) -> bool:
    _require_known(name)
    return _VALIDATORS[name].is_valid(instance)


def iter_errors(name: str, instance: dict) -> list[str]:
    """Return human-readable validation errors ([] if valid)."""
    _require_known(name)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(_VALIDATORS[name].iter_errors(instance), key=str)
    ]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def wrap(payload: dict, run_id: str, produced_by: str, produced_at: str | None = None) -> dict:
    """Stamp the shared envelope onto a payload (mirrors contract_store_tool, 04 §5.17)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "produced_by": produced_by,
        "produced_at": produced_at or _now_iso(),
        **payload,
    }


# ---- fixture factory: minimal valid instance per contract ----
_SAMPLE_PAYLOADS: dict[str, dict] = {
    "event": {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "source": "github",
        "repo": {"url": "https://github.com/acme/python-payments-service", "name": "python-payments-service", "default_branch": "main"},
        "change": {"base_sha": "aaa000", "head_sha": "bbb111", "branch": "feature/x",
                   "pr_id": "42", "title": "Add refund endpoint", "description": "…", "author": "dev@acme.io"},
        "target_transition": {"from_env": "dev", "to_env": "test"},
        "requested_by": "dev@acme.io",
        "received_at": "2026-07-08T00:00:00+00:00",
    },
    "change_profile": {
        "files": [{"path": "app/auth/login.py", "language": "python", "change_type": "modified",
                   "hunks": [], "functions_changed": [{"name": "login", "kind": "function", "line_start": 10, "line_end": 25, "is_new": False}]}],
        "new_functions": [],
        "classification": "feature",
        "loc_added": 30, "loc_removed": 4,
        "blast_radius": {"direct": ["app.auth"], "transitive": ["app.api"], "count": 2},
        "sensitive_flags": [{"flag": "auth", "matched_by": "auth", "files": ["app/auth/login.py"]}],
    },
    "security_findings": {
        "findings": [{"id": "SEC-001", "category": "owasp:sqli", "severity": "critical",
                      "file": "app/auth/login.py", "line_start": 12, "line_end": 12, "cwe": "CWE-89",
                      "title": "SQL injection", "explanation": "…", "fix_suggestion": "parameterize", "source": "tool"}],
    },
    "quality_findings": {
        "findings": [{"id": "QUAL-001", "category": "complexity", "severity": "low",
                      "file": "app/api.py", "line_start": 1, "line_end": 40,
                      "title": "High cyclomatic complexity", "explanation": "…", "fix_suggestion": "split", "source": "llm"}],
        "quality_score": 82,
    },
    "review_plan": {
        "mode": "audit",
        "budget_lines": 800,
        "shards": [{"shard": 1, "label": "app", "files": ["app/auth/login.py"], "hotspot_weight": 6.0}],
        "metrics": {"files_scanned": 12, "excluded_files": 3, "added_lines": 640,
                    "hotspot_lines": 41, "shard_count": 1, "basis": "hotspot lines / budget"},
    },
    "senior_summary": {"summary": "One critical SQL injection in auth; overall posture poor."},
    "review_report": {
        "executive_summary": "1 critical, 1 low.",
        "findings": [{"id": "SEC-001", "severity": "critical", "title": "SQL injection", "source": "tool"}],
        "pr_health_score": 55,
        "recommendation": "request_changes",
        "counts": {"critical": 1, "high": 0, "medium": 0, "low": 1},
        "coverage": {"total_added_lines": 640, "llm_reviewed_lines": 41,
                     "deterministic_coverage_pct": 100, "shards": 1, "unscanned_shards": []},
    },
    "test_plan": {
        "selected": [{"test_id": "tests/test_auth.py::test_login", "reason": "covers changed login()", "mapping_source": "import_graph"},
                     {"test_id": "tests/test_health.py::test_health_ok", "reason": "smoke", "mapping_source": "smoke"}],
        "smoke_set": ["tests/test_health.py::test_health_ok"],
        "excluded_summary": "312 excluded (unaffected)",
        "selection_confidence": "high",
        "estimated_runtime_seconds": 12,
    },
    "test_results": {
        "runner": "pytest", "command": "pytest tests/test_auth.py::test_login --junitxml=out.xml -q",
        "totals": {"passed": 2, "failed": 0, "skipped": 0, "errors": 0},
        "cases": [{"test_id": "tests/test_auth.py::test_login", "status": "passed", "duration_ms": 40}],
        "coverage_delta": {"line_pct_before": 71.0, "line_pct_after": 72.5},
        "duration_seconds": 0.9, "timed_out": False,
    },
    "env_context": {
        "target_env": "test",
        "incidents": {"count_7d": 0, "count_30d": 1, "most_recent_at": "2026-06-20T00:00:00+00:00"},
        "deploy_window": {"risky": False},
        "env_stability": "stable",
        "batch_size_commits": 3,
        "flags": [],
        "summary": "Low-risk window, stable env.",
    },
    "risk_score": {
        "score": 55, "band": "high", "formula_version": "risk-v1",
        "contributions": [{"factor": "security.critical", "points": 40.0, "cap_applied": False, "evidence_ref": "SEC-001"},
                          {"factor": "change.sensitive_flag", "points": 15.0, "cap_applied": False, "evidence_ref": "auth"}],
        "llm_escalation": {"points_added": 0, "justification": ""},
        "explanation": "security.critical +40 (SEC-001); change.sensitive_flag +15 (auth)",
    },
    "decision": {
        "decision": "escalate",
        "transition": {"from_env": "qa", "to_env": "staging"},
        "policy_version": "ladder-v1",
        "rule_fired": "qa->staging/high",
        "reasoning_trail": {"review": "1 critical", "testing": "all green", "results": "0 failed",
                            "context": "stable", "policy": "high on qa->staging escalates"},
        "actions_taken": [{"action": "queue_escalation", "detail": "approval queued", "at": "2026-07-08T00:00:01+00:00"}],
        "approval_required": True,
        "approval_status": "pending",
    },
}


# shard-findings aliases reuse the security_findings fixture (same schema)
for _i in range(1, 5):
    _SAMPLE_PAYLOADS[f"security_findings_shard_{_i}"] = dict(_SAMPLE_PAYLOADS["security_findings"])


def sample(name: str, run_id: str = "00000000-0000-0000-0000-000000000000") -> dict:
    """Return a valid, envelope-stamped instance of contract `name`."""
    _require_known(name)
    return wrap(dict(_SAMPLE_PAYLOADS[name]), run_id=run_id,
                produced_by=f"fixture:{name}", produced_at="2026-07-08T00:00:00+00:00")


def demo() -> None:
    """Round-trip self-check: every fixture validates; a corrupted one fails."""
    for name in CONTRACTS:
        inst = sample(name)
        errs = iter_errors(name, inst)
        assert not errs, f"fixture for {name} invalid: {errs}"
        assert is_valid(name, inst)

    # negative: bad band must fail
    bad = sample("risk_score")
    bad["band"] = "extreme"
    assert not is_valid("risk_score", bad), "bad band should fail validation"

    # negative: missing envelope field must fail
    missing = sample("event")
    del missing["run_id"]
    assert not is_valid("event", missing), "missing run_id should fail validation"

    print(f"contracts OK: {len(CONTRACTS)} contract keys, all fixtures valid, negatives rejected")


if __name__ == "__main__":
    demo()
