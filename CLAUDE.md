# CLAUDE.md

## Project

**Sentinel** — Cognizant internal hackathon project: multi-agent code review + smart test selection + explainable promotion gating, built on **Neuro-SAN as the sole multi-agent orchestrator**. **Implementation in progress** (host-native dev, no Docker); `docs/solution/` remains the spec. See **Implementation status** below.

## Repo layout

- `docs/hackathon-delivery-intelligence.md` — original problem statement (source of the idea; do not edit).
- `docs/personal-project-delivery-intelligence-full.md` — post-hackathon evolution vision. Architecture must stay compatible with it, but hackathon docs must **never mention** these future features (test generation, mutation testing, learning loop).
- `docs/solution/` — the design document set. `01-proposed-solution.md` is the **master spec / single source of truth**; 02 (DFD), 03 (HLD), 04 (LLD), 05 (architecture) derive from it.
- `neuro-san-studio/` — clone of cognizant-ai-lab/neuro-san-studio. **Read-only reference** for framework facts (HOCON schema, AAOSA, CodedTool interface, server/deploy). Never modify it.
- `graphify-out/` — knowledge graph of the docs (gitignored, regenerable).
- **Implementation** (layout per [04 §1](docs/solution/04-lld.md)): `db/` (Postgres: Alembic migrations, SQLAlchemy models, shared DAO — schema `sentinel`) · `lib/` (`contracts.py` = 9 data contracts + validators + fixtures, `redact.py`, `workspace.py`) · `config/` (llm + risk/ladder/repo) · `coded_tools/sentinel/` (coded tools; `AGENT_TOOL_PATH=coded_tools`, refs resolve `sentinel.<module>.<Class>`) · `registries/` (network HOCON + manifest, network name `sentinel` — empty until B-slices) · `tests/` (pytest). `.env` = host-native config, gitignored (holds the NIM key).

## Hard rules

1. Any design change starts in `01-proposed-solution.md`, then propagates to 02–05. Docs must never contradict 01.
2. Framework claims must be grounded in `neuro-san-studio/` (docs or code) — no guessed Neuro-SAN behavior. Key refs: `docs/user_guide.md`, `registries/*.hocon`, `coded_tools/`.
3. Locked design decisions (revisit only if user asks): NVIDIA NIM primary LLM — documented primary `nvidia-llama-3.3-70b-instruct`, but it **times out on the public NIM endpoint**, so the **working dev model is `mistralai/mistral-small-4-119b-2603`** (set via `.env` `MODEL_NAME`; revert when 70B access exists). **neuro-san NIM config MUST use `{class: nvidia, model_name: "<raw NIM id>"}`** — a bare model key passed with `class` is sent unmapped → 404 (verified). PostgreSQL everywhere — **dev is host-native: local Postgres 17, dedicated schema `sentinel`, no Docker**; docker-compose/K8s reserved for packaging/prod (move is config-only). GitHub Actions for hackathon, Jenkins + GitLab CI post-hackathon (decided 2026-07-07); Mermaid for all diagrams.
4. Core design principle in every decision: **LLM reasons, code decides** — risk formula, trust ladder, test execution are deterministic coded tools; LLM may only raise risk, never lower it; staging→production never auto-promotes.
5. Naming: HOCON coded-tool names drop the `_tool` suffix; module files keep it (`test_runner` ↔ `coded_tools/sentinel/test_runner_tool.py`, class `TestRunnerTool`).

## Knowledge graph (graphify)

`graphify-out/graph.json` maps all docs (120 nodes, 14 communities). Before answering cross-document questions, prefer querying it (`/graphify query "<question>"`) over re-reading the full doc set. After editing docs, refresh with `/graphify docs --update`. **Stale after the sentinel rename + layout changes — refresh before relying on it.**

## Implementation status (2026-07-08)

**Done + pushed:** rename `delivery_intelligence`→`sentinel` (identifier + brand; domain nouns kept); host-native env; DB baseline (13 tables, schema `sentinel`, Alembic `0001`); config files; `lib/contracts.py` (9 contracts) + `redact` + `workspace`; **A6** `risk_calculator` (deterministic `risk-v1`, raise-only LLM escalation) + `trust_ladder` (band→decision, prod hard-floor, fail-closed) with 12 passing tests. `db/` + `lib/` promoted to top-level in 04 §1. Every non-trivial module ships a runnable self-check.

**Done + pushed (2026-07-08):** **Framework spike (0.4)** `5ac6f43` (findings + framework facts in [07 §3.1](docs/solution/07-implementation-plan.md)) · **0.3 sample repo + A1** `bbf0071` · **A2/A3 + §5 I/O refactor** `06cc5c4` · **A7** `92b2c41` · **A5** `d9e8af6` · **A4** `c8e19e6`. **✅ TRACK A COMPLETE — all 17 coded tools (A1–A7), stdlib-only (zero new runtime deps), 42 unit tests passing.** LLM-config facts locked: form-B raw id + `custom_llm_info` alias→key pattern; coded-tool param types `string|int|float|boolean|array|object`.

**Done + pushed (2026-07-08, `58ab7de`):** **B1** — first network slice `registries/sentinel.hocon` (frontman + `change_analysis_agent` + A1 tools) + `manifest.hocon` + `aaosa_basic.hocon`. Verified live (`scripts/verify_b1.py`). 3 framework fixes: (1) `fallbacks[0]` is effective primary (mistral first in `llm_config.hocon`); (2) coded-tool `function.parameters` needs ≥1 property (nominal `reason` arg); (3) AAOSA reserved for parallel fan-out, linear slices use plain instructions.

**Done + pushed (2026-07-09, `7c5f05a`):** **B2 → ✅ M2** (review report headless). `security_review_agent` + `code_quality_agent`; frontman fans out change→security→quality→report_publisher. Verified live (`verify_b2.py`): AWS key + SQLi → 3 criticals, health 24, request_changes. Two framework-forced design decisions (backport to 01 like §9): (a) diff reaches the security LLM via `secret_scanner.added_lines` (LLMs can't read sly_data); (b) review synthesis done in code by `report_publisher` (rule 4).

**Done + pushed (2026-07-09, `7ad6b7d`):** **B3** — `test_selection_agent` + `test_mapper` + `test_runner`; 6-step chain. Verified live (`verify_b3.py`): real pytest subset ran (`passed:1`). `test_mapper` finalizes `test_plan` into sly_data (add-only via `added_test_ids`).

**Done, not yet committed (2026-07-09):** **B4 → ✅ M3 reached** — full 9-step pipeline headless, both demo runs pass (`scripts/verify_b4.py`): happy (dev→test, benign) → risk 0/low → **promote**; insecure (qa→staging, secret+SQLi) → risk 100/critical, 3 criticals, health 25 → **escalate** (`request_changes`, trail cites the policy rule + risk). Added `environment_context_agent` + `risk_scoring_agent` + `promotion_gating_agent` + A5/A6/A7 tool defs. `trust_ladder` now writes `ladder_verdict` to sly_data; `decision_logger` builds the full Decision in code from sly_data (same rule-4 pattern as `report_publisher`). Unit suite **43 passing**. **⚠️ Batching risk (§14):** on the long chain mistral can emit parallel tool calls, racing the dependency-ordered tail (report/risk/gating ran before the slower security agent finished → missed criticals). Mitigated by a firm frontman rule ("issue exactly ONE tool call at a time, wait for the result"); both runs then passed. **Prompt-mitigated, not guaranteed — re-run `verify_b4` several times during Sat hardening; if flaky, collapse the deterministic tail (report+risk+ladder+decision) into one coded tool.**

**Done, not yet committed (2026-07-09):** **Track C — Delivery Gateway** (`gateway/`) demo-critical core. `app.py` (FastAPI: `POST /api/v1/simulate`, runs list/detail, `/events` SSE, approvals+resolve, rerun, audit, healthz; token→role auth shim; run state machine `received→analyzing→reviewing→testing→scoring→gated→done|failed` driven by streamed progress; in-memory SSE bus with durable replay) + `settings.py` + `invoker/neuro_san_client.py` (streaming client, `chat_filter: MAXIMAL`, progress→state, allow-listed sly_data extraction) + `scripts/run_gateway.py` + `scripts/verify_c.py`. Two framework/design facts: (a) with MAXIMAL the progress signal is `type=AGENT` text ``Invoking: `<name>` `` (NOT AGENT_FRAMEWORK — that's just the terminal marker); (b) only `review_reports`/`decisions` are tool-persisted, so the **Gateway persists `risk_score`/`test_results`/`test_plan`/`env_context` from the returned sly_data on finalize** (backport to 01 like §9). Live-verified `verify_c.py`: simulate→real network→`done`→**promote**, risk persisted, **49 SSE progress events**. Unit suite **48 passing** (43 + 5 gateway). Track state: **TASKS.md** at repo root is the live tracker.

**Done, not yet committed (2026-07-09):** **Track D — Dashboard SPA** (`frontend/`, → M4 built). Stack **deviates from 06 §3** (ponytail): plain **React 19 + Vite 7 + Tailwind v4 + React Router + fetch + native EventSource** — dropped shadcn/Recharts/TanStack to cut moving parts; same screens. Files: `src/{types,lib,sse,App,RunDetail}.tsx`. Screens: Runs list (filters + chips), Run detail (StageTimeline SSE + ReviewReport/TestPlan/TestResults/RiskScore-dial+bars+LLM-escalation-badge/Decision+5-section-trail+prod-lock cards), Approvals (mandatory reject comment, RoleGate), Audit, `/runs/compare` side-by-side. Auth = token→role shim. `npm run build` clean (250 kB). **Wire contract verified live**: started Gateway on :8000, `GET /api/v1/runs` + `/runs/{id}` shapes match the TS types exactly (dial 2 contribs, 5-section trail, health gauge). Browser render pass deferred to 6.2. **Note:** Vite 8 (rolldown) native binding breaks on this Windows box → pinned **vite 7** + `@vitejs/plugin-react` 4.

**Next (critical path, [07](docs/solution/07-implementation-plan.md)):** M0–M4 done; all tracks built. Remaining = **Phase 6**:
1. **6.1/6.2 integration** — escalate (Demo-2) run through the Gateway; cloned-repo run (not just `repo_workspace` override); point SPA at Gateway, browser render + live SSE timeline.
2. **6.3/6.4** demo scripts + seeded incident + full rehearsal (Run 1 promote, Run 2 escalate→approve live, compare view).
3. **6.5** batching re-runs (§14) + failure drills; **6.6** README.
- **Design deviations to backport to 01** (framework-forced, like §9): synthesis/decision in code; tools return data to LLMs; AAOSA for parallel fan-out; frontman one-tool-at-a-time; **Gateway-side contract persistence**; **invoker MAXIMAL filter**; **SPA stack simplification**.
- **Run servers:** Neuro-SAN `python -m neuro_san.service.main_loop.server_main_loop` (:8080, export `.env` first) · Gateway `PYTHONPATH=. GATEWAY_PORT=8000 python scripts/run_gateway.py` · SPA `cd frontend && npm run dev` (:5173, proxies /api→8000).

Runner note: the studio nsflow GUI is wired to NIM for manual testing (`neuro-san-studio/config/llm_config.hocon` local override; revert with `git -C neuro-san-studio checkout config/llm_config.hocon`).

## Conventions

- Diagrams: Mermaid in fenced blocks (GitHub/VS Code renderable). Avoid raw `{}` in flowchart labels.
- Section cross-references between docs use the form `[04-lld.md §5](04-lld.md)` — verify the target section exists after renumbering.
