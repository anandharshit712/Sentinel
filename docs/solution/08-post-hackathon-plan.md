# 08 — Post-Hackathon Plan: Phase 2 (Full Review Cluster)

**Status:** planning → in progress · **Started:** 2026-09-20 · **Author:** Harshit Anand

This is the first document **outside the hackathon set**. Documents 01–07 describe the delivered
hackathon system and stay frozen at that scope — per the repo rule they must never mention features
from the evolution vision. This document and its successors carry the post-hackathon track.

- **Target architecture:** [`docs/personal-project-delivery-intelligence-full.md`](../personal-project-delivery-intelligence-full.md) — the 13-agent platform and its five-phase roadmap.
- **This document:** vision **Phase 2 — Full Review Cluster**, only.
- **Deliberately not here:** test generation, mutation-grounded evaluation, the learning loop
  (vision Phases 3–5), and the production track (Phase 7 in [07 §11](07-implementation-plan.md)).

---

## 1. What Phase 2 is

The hackathon shipped two reviewers — Security (fanned out 1–4 ways plus a senior summarizer) and
Code Quality. The vision's review cluster has four specialists. Phase 2 adds the missing two:

| Agent | Looks for |
|---|---|
| ⚡ **Performance Review** | algorithmic inefficiency, N+1 queries, blocking I/O in async contexts, unbounded allocation in loops, missing pagination/caching on hot paths |
| 📜 **Compliance & License** | dependency license conflicts (GPL in a permissive codebase), PII in logs or URLs, regulated-data patterns, deprecated API usage |

### Multi-language: already satisfied

The vision pairs Phase 2 with "extend to 2–3 languages". That shipped in the hackathon build:
Python (`ast`, pytest) and JS/TS (tree-sitter, jest), both manifest-detected. Java/Go/Rust stay out
of scope here for the reason recorded in `CLAUDE.md` — per-language grammar **and** import
resolution **and** a test-runner adapter each, which is a phase of its own, not a config flag.

**Scope of Phase 2 = two agents, their deterministic floors, and their contracts.** Nothing else.

---

## 2. Design constraints carried from the hackathon build

These are not re-litigated here; they constrain every item below.

1. **Rule 4 — LLM reasons, code decides.** Each new reviewer gets a *deterministic floor*: coded
   rules that fire regardless of what the LLM says, exactly as `secret_scanner` does for security.
   The LLM may add findings and explain them; it can never remove a floor finding.
   `report_publisher` recomputes the floor itself ([07 §14](07-implementation-plan.md)), so a
   reviewer that drifts, stalls or hallucinates cannot produce a falsely clean report.
2. **The chain is the scarce resource.** The frontman already runs a 12-step sequence and drifts on
   long chains (§14). Two more LLM hops is the single largest risk in this phase — see §6.
3. **Provider reality.** NIM throws intermittent HTTP 500s and retires models without warning
   (`docs/model-evaluation.md` §8). More agents = more calls = more exposure. Re-probe with
   `scripts/probe_models.py` before blaming the code.
4. **Contracts are additive.** New sly_data keys, no changes to existing ones, so every current
   consumer (Gateway persistence, SPA cards, risk formula) keeps working untouched.

---

## 3. Work items

| # | Item | Detail | Test |
|---|---|---|---|
| **P2.1** | Contracts | `performance_findings`, `compliance_findings` in `lib/contracts.py`, both reusing the existing `_FINDINGS` schema; sample payloads for the fixture set | contract validators accept the fixtures, reject a bad severity |
| **P2.2** | `lib/perf_rules.py` | Deterministic performance smells over added lines: query/HTTP call inside a loop (N+1), `await` inside a loop, blocking call in an `async def`, nested loop over a collection parameter, `SELECT *` without `LIMIT`. Kept **out of** `lib/triage.SINK_RULES` on purpose: that list feeds the *security* floor and the hotspot ranking, and a slow loop is not a vulnerability | every rule fires on a positive and stays silent on a benign near-miss |
| **P2.3** | `lib/license_rules.py` | Project's own declared licence (`package.json`, `pyproject.toml`, `LICENSE`); copyleft notices in changed files (vendored GPL — the licensing accident that actually reaches a diff); a manifest relicensing itself; PII written to a log or URL; deprecated APIs. **Resolving each dependency's licence is deliberately out of scope** — it needs a registry lookup per dependency, with caching and offline-failure handling, which is a feature of its own. The tool says so in its output rather than implying an audit it did not do | copyleft notice in a permissive project fires `high`; `"license": "GPL-3.0-only"` in a manifest fires, `"MIT"` does not, `LGPL` downgrades to medium; `log.info(user.email)` fires, `log.info("count=%d")` does not |
| **P2.4** | `performance_scanner` coded tool | Mirrors `secret_scanner`: returns deterministic findings **plus** the added-line snippets the LLM needs (LLMs cannot read sly_data — [07 §9](07-implementation-plan.md)) | tool returns findings + `added_lines`; honours `exclude_globs` |
| **P2.5** | `license_scanner` coded tool | Same shape, over manifests rather than the diff; reports `licenses_seen` for the report's provenance | fires on a planted GPL dependency |
| **P2.6** | Two agents in `registries/sentinel.hocon` | `performance_review_agent` (tools: `performance_scanner`, `complexity_metrics`, `contract_store`) and `compliance_review_agent` (tools: `license_scanner`, `dependency_cve`, `contract_store`), inserted after `code_quality_agent`; frontman steps renumbered 12 → 14 | live run writes both contracts |
| **P2.7** | `report_publisher` merge | Add the two keys to the merge list, extend the deterministic floor with the perf/licence floors, keep dedup and health arithmetic in code | unit test: four finding sources dedup into one ranked report |
| **P2.8** | Gateway + SPA | Nothing to change if the findings carry `category` — they flow through `review_report`. Verify the Review Report card groups them legibly; add a category filter only if it reads badly | UI check on a run with all four sources |
| **P2.9** | `scripts/verify_p2.py` | Fixture repo planting one finding per dimension — hardcoded AWS key (security), `db.query()` inside a `for` (performance), user email written to a log (compliance) — and asserting all three reach the merged report, that no non-security finding claims `critical`, and that the run still produces a decision. Takes a trial count so the decision rate is a number, not an impression | 3/3 runs produce a decision |

---

## 4. Contract shape

Both reuse `_FINDINGS`, so nothing downstream needs to learn a new shape:

```json
{
  "schema_version": "1", "run_id": "...", "produced_by": "performance_review",
  "produced_at": "...",
  "findings": [{
    "id": "PERF-001", "category": "n_plus_one", "severity": "medium",
    "file": "app/orders.py", "line_start": 42, "line_end": 48,
    "title": "Database query inside a loop",
    "explanation": "...", "fix_suggestion": "...", "source": "tool"
  }]
}
```

`source` stays `tool` for floor findings and `llm` for reviewer additions — the existing
distinction that makes it possible to ask "what did the LLM layer actually contribute?"

**Risk formula: unchanged.** New findings reach `risk_calculator` through the severity counts in
`review_report`, which is what the formula already consumes. Changing weights would be a design
change and would have to start in [01](01-proposed-solution.md) — it is not part of this phase.

---

## 5. Severity discipline

A performance smell is not a security hole, and the risk score must not pretend otherwise.

- Performance findings cap at **medium** from the deterministic floor; the LLM may argue for
  `high` on a hot path, and that is exactly the kind of judgment it is there for.
- Compliance: licence conflicts are **high** (they are legal blockers, and they are certain);
  PII-in-logs is **high**; deprecated APIs are **low**.
- Nothing in this phase emits `critical`. Critical stays the security floor's word, so that
  "this run has a critical" keeps meaning the thing it means today.

---

## 6. The real risk: chain length — measured, and resolved by collapsing the tail

**Status: done.** The risk was real, it was measured, and the pre-registered mitigation fixed it.

### What the measurements showed

| Configuration | contract_store calls/run | Review contracts written | Decision rate |
|---|---|---|---|
| Before Phase 2 (12 steps) | ~89 | 1 of 5 | 3/3 |
| Phase 2, 14 steps (2 new agents) | ~89 | 1 of 5 | 3/3 |
| + `contract_store` accepts a JSON-string payload | ~5 | **5 of 5** | **1/3** |
| + deterministic tail collapsed (12 steps) | ~5 | 5 of 5 | **3/3** |

Two separate defects, and the first one masked the second.

### Defect 1 — the LLM review layer was never reaching the report

Models send `contract_store`'s `payload` as a JSON *string* roughly as often as an object. The tool
required an object, returned an error, and the agent retried the identical call until it gave up:
**89 calls, 1 write, in a single run.** Because `report_publisher` recomputes the deterministic
floor, the report still had findings and every run looked green.

This was not a Phase 2 bug. It affected `security_findings_shard_n`, `quality_findings` and
`test_plan` too — every LLM-produced contract in the project. It also explains the open question
recorded in `CLAUDE.md` on 2026-08-16 ("the LLM reviewers added zero findings beyond the
deterministic floor — untested whether that's correctness or under-reporting"): neither. Their
writes were being rejected.

Fixed by parsing a string payload before validating it — lenient at the door, strict at the schema
— and, just as importantly, by **logging every rejection**. The missing log line is why a defect
this total stayed invisible for two months.

### Defect 2 — the drift the plan predicted

With the review agents finally returning findings, the frontman conversation grew and the chain
started dying in the tail: two of three runs wrote every review contract and then never reached a
verdict. A run that reviews the code, runs the tests and produces no decision is worth nothing.

Mitigation 2 from the original plan, applied: `environment_context_agent`, `risk_scoring_agent` and
`promotion_gating_agent` are replaced by one coded tool, `finalize_run`, which composes the same
tools in process. Reading their instructions confirmed none of them decided anything — each called
coded tools in a fixed order and copied results forward, at the cost of a turn each.

- Chain: 14 steps → **12**, with the last three LLM hops replaced by one deterministic call.
- Decision rate: 1/3 → **3/3**, with the full LLM review layer active.
- **Given up:** the raise-only `llm_escalation` on the risk score. Deferred, not replaced — risk is
  now purely `risk-v1` over the stored contracts, which is the safer direction (code decides).
  Restoring it means passing an escalation argument into `finalize_run`, not reviving the agent.
- **Framework fact learned:** neuro-san validates that every declared tool is reachable from the
  frontman and rejects the whole registry otherwise ("Unreachable agents found"), taking the
  network offline with a 404 rather than a startup error. The seven tail tools had to be removed
  from `registries/sentinel.hocon`, not merely unhooked.

## 7. Exit criteria

- [ ] Both agents write their contracts on a live run; `report_publisher` merges four sources.
- [ ] Planted N+1, blocking-async call, GPL dependency and PII-in-log all appear in the report with
      the right category and severity, and at least one is a floor (`source: tool`) finding.
- [ ] `verify_b4.py` still passes 3/3 — no regression on the existing demo runs.
- [ ] `verify_p2.py` passes 3/3.
- [ ] Unit tests green (`pytest -q`), including the new rule tables.
- [ ] Decision rate measured before and after, and recorded here.

---

## 8. After this

Vision Phase 3 (Coverage Gap → Test Generation → mutation-grounded Test Quality Evaluation) is the
headline of the personal project and gets its own document. It should not start until the chain
length question in §6 is settled — Phase 3 adds three more agents, and doing that on a chain that
already drifts would make the results unreadable.
