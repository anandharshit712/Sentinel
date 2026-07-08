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

**Next (critical path, [07](docs/solution/07-implementation-plan.md)):** Full agent network done through **M3**. Remaining:
1. **Track C — Delivery Gateway** (`gateway/`): `POST /api/v1/simulate` + `invoker/neuro_san_client.py` (streaming HTTP client, the `verify_b*.py` pattern is the template) + run state machine + REST/SSE + GitHub adapter. Swap `verify_*` scripts for a real HTTP entrypoint.
2. **Track D — Dashboard SPA**. Then Phase 6 integration + demo hardening (incl. re-running the demo runs several times, and the batching hardening above).
- **B-slice design deviations to backport to 01** (framework-forced, like §9): synthesis/decision done in code (report_publisher, decision_logger); tools return data to LLMs since sly_data is LLM-invisible; AAOSA reserved for parallel fan-out; frontman "one tool call at a time" ordering rule.

Runner note: the studio nsflow GUI is wired to NIM for manual testing (`neuro-san-studio/config/llm_config.hocon` local override; revert with `git -C neuro-san-studio checkout config/llm_config.hocon`).

## Conventions

- Diagrams: Mermaid in fenced blocks (GitHub/VS Code renderable). Avoid raw `{}` in flowchart labels.
- Section cross-references between docs use the form `[04-lld.md §5](04-lld.md)` — verify the target section exists after renumbering.
