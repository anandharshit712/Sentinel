# Sentinel — Project Task List

Live tracker derived from [07-implementation-plan.md](docs/solution/07-implementation-plan.md). Updated after each item completes.

**Legend:** `[x]` done · `[~]` partial/deferred · `[ ]` not started · `[N/A]` intentionally out of hackathon scope.

**Status @ 2026-07-09:** critical path **M0→M3 complete**; **Track C core live-verified** (`verify_c.py`: simulate→real network→`done`→promote, SSE 49 events, contracts persisted). Next up: **Track D — Dashboard SPA**.

---

## Milestones

| Milestone | Proof                                                                              | Status                                   |
| --------- | ---------------------------------------------------------------------------------- | ---------------------------------------- |
| **M0**    | Spine end to end → decision (now via Gateway `POST /simulate`, `verify_c.py`) | `[x]` |
| **M1**    | Contracts frozen (v1); DB migrates; config loads                                   | `[x]`                                    |
| **M2**    | Review report from event, headless                                                 | `[x]`                                    |
| **M3**    | Full pipeline headless, both demo runs                                             | `[x]`                                    |
| **M4**    | Dashboard renders all screens from mocks                                           | `[ ]`                                    |
| **M5**    | Scripted Demo Runs 1 & 2 green on clean run, twice                                 | `[ ]`                                    |

---

## Phase 0 — Skeleton, Framework Spike, Tracer Bullet

- [x] **0.1** Repo scaffold per [04 §1](docs/solution/04-lld.md) (`registries/`, `coded_tools/sentinel/`, `config/`, `lib/`, `db/`, `samples/`, `tests/`); Python 3.12, `neuro-san` pinned (0.6.71). — _host-native, no `gateway/`/`frontend/`/`deploy/` yet (built in C/D)_
- [N/A] **0.2** `deploy/docker-compose.yaml` — **deferred to packaging/prod** (host-native dev decision; move is config-only).
- [~] **0.3** Sample repos: `samples/python-payments-service` (Flask + pytest, Demo-2 SQLi plant site). — _python `[x]`; `node-catalog-service` `[N/A]` (cut-line, Python-only demo)_
- [x] **0.4** Framework spike — all 6 assumptions confirmed; findings in [07 §3.1](docs/solution/07-implementation-plan.md) + `reference-neuro-san-facts` memory.
- [x] **0.5** Tracer bullet — Gateway `POST /api/v1/simulate` → real network → decision, verified `scripts/verify_c.py`.

## Phase 1 — Foundations

- [x] **1.1** `lib/contracts.py` — 9 contracts + validators + fixtures.
- [x] **1.2** `db/migrations/` — Alembic baseline `0001` (13 tables, schema `sentinel`).
- [x] **1.3** `db/dao.py` — SQLAlchemy DAO (+ `insert_decision`, `insert_notification`, `save_run_payload`, `recent_incidents`).
- [x] **1.4** `lib/workspace.py` (+ `run_inputs`) + `lib/redact.py`.
- [x] **1.5** Config: `risk_weights_v1.yaml`, `trust_ladder_policy.yaml`, `repo_config.yaml`, `llm_config.hocon` + `custom_llm_info.hocon`, `osv_snapshot.json`.
- [ ] **1.6** Recorded webhook payloads (GitHub PR) as simulate fixtures under `tests/fixtures/`. — _only needed for C3 GitHub adapter_

## Track A — Coded Tools (17 tools, 7 groups) — ✅ COMPLETE

- [x] **A1** `git_diff`, `ast_analyzer`, `dependency_graph`
- [x] **A2** `secret_scanner`, `dependency_cve`
- [x] **A3** `complexity_metrics`
- [x] **A4** `test_mapper`, `test_runner`
- [x] **A5** `incident_history`, `deploy_window`
- [x] **A6** `risk_calculator`, `trust_ladder` (highest-value; table-driven tests)
- [x] **A7** `report_publisher`, `decision_logger`, `cicd_action`, `notification`, `contract_store`
- [x] Stdlib-only (zero new runtime deps); **43 unit tests passing**.

## Track B — Agent Network (HOCON slices) — ✅ COMPLETE through M3

- [x] **B1** frontman `delivery_coordinator` + `change_analysis_agent` (+A1). Verified `verify_b1.py`.
- [x] **B2** `security_review_agent`, `code_quality_agent`, `report_publisher` synthesis (+A2/A3/A7). **→ M2.** Verified `verify_b2.py`.
- [x] **B3** `test_selection_agent` + `test_mapper`/`test_runner` (+A4). Verified `verify_b3.py` (real pytest subset ran).
- [x] **B4** `environment_context_agent`, `risk_scoring_agent`, `promotion_gating_agent` (+A5/A6/A7). **→ M3.** Both demo runs verified `verify_b4.py`.
- [ ] **B-hardening** re-run `verify_b4.py` several times (batching risk §14); if flaky, collapse deterministic tail (report+risk+ladder+decision) into one coded tool. — _Saturday_

## Track C — Delivery Gateway — ✅ demo-critical core done

- [x] **C1** `gateway/app.py`, `settings.py`, DB wiring, run state machine (`received→analyzing→reviewing→testing→scoring→gated→done|failed`), `POST /api/v1/simulate`, workspace clone + cleanup, idempotent `event_id`. TestClient suite (5) + `scripts/run_gateway.py` launcher.
- [x] **C2** `gateway/invoker/neuro_san_client.py` — streaming client (MAXIMAL filter), progress→state mapping, `done` + allow-listed sly_data extraction, contracts persisted to DB on finalize, stream-break ⇒ `failed`. Live-verified `scripts/verify_c.py`.
- [~] **C4** REST + SSE: runs list/detail, `/events` SSE relay (in-memory bus, durable replay), approvals queue + resolve (mandatory reject comment), rerun, audit, token→role auth shim — **done**. Deferred: `/internal/publish-report` + `/internal/cicd-action` (only needed for real CI/CD; `SIMULATE_CICD` no-op covers the demo).
- [ ] **C3** GitHub adapter (HMAC verify, normalize, gate status, PR comment, dispatch) + webhook route. — _off the demo path (simulate is demo mode); needs 1.6 fixtures_
- [N/A] **C5** Jenkins + GitLab adapters — **Phase 7** (post-hackathon).

## Track D — Dashboard SPA

- [ ] **D1** Vite + React + TS + Tailwind + shadcn scaffold, router (5 routes), auth shim, wire types.
- [ ] **D2** Runs list + shared chips/badges (`BandChip`, `DecisionChip`, `SeverityChip`).
- [ ] **D3** Run detail cards (ReviewReport, TestPlan, TestResults, RiskScore dial + bars + escalation badge, Decision + trail); StageTimeline.
- [ ] **D4** SSE hook (`useRunEvents`) + polling fallback; live→durable switchover.
- [ ] **D5** Approvals queue (mandatory reject comment), Audit, `/runs/compare`. — _D5 compare view is a cut-line item_

## Phase 6 — Integration & Demo Hardening

- [~] **6.1** Gateway ↔ real Neuro-SAN — built directly against the real server (no stub); `verify_c.py` proves state machine advances off real progress markers + allow-listed sly_data arrives + contracts persist. Remaining: run against a **cloned** repo (not just `repo_workspace` override) + insecure/escalate path through the Gateway.
- [ ] **6.2** Point SPA at real Gateway; SSE live timeline against a real run.
- [ ] **6.3** `scripts/demo_run_1.sh` / `demo_run_2.sh`; seeded `incidents` row for score-shift.
- [ ] **6.4** Full rehearsal: Run 1 auto-promote, Run 2 escalate→approve live, `/runs/compare`, NSFlow second screen.
- [ ] **6.5** Hardening: log-redaction on real logs, load smoke (LLM stubbed), failure drills (kill NIM key, kill test run). — _cut-line: load smoke_
- [ ] **6.6** README: quickstart, demo script, architecture pointer.

## Phase 7 — Production Track (post-hackathon)

- [N/A] K8s manifests · `RUNNER_MODE=k8s` · OIDC + roles · ExternalSecrets · OTEL/Prometheus · live GitHub webhook · Jenkins/GitLab adapters · Slack/Teams webhook.

---

## Backlog / carry-forward

- [ ] **Backport B/C-slice deviations to [01](docs/solution/01-proposed-solution.md)** (then propagate 02–05), like the §9 items: synthesis/decision done in code (`report_publisher`, `decision_logger`); tools return data to LLMs (sly_data is LLM-invisible); AAOSA reserved for parallel fan-out; frontman "one tool call at a time" ordering rule; **Gateway persists risk_score/test_results/test_plan/env_context from returned sly_data on finalize** (only review_reports/decisions are tool-persisted); **invoker uses `chat_filter: MAXIMAL`** for the progress timeline.
- [ ] Refresh graphify knowledge graph after doc edits (stale since rename + layout changes).
