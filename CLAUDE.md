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

**Done + pushed (2026-07-08, `5ac6f43`):** **Framework spike (0.4)** — all 6 assumptions confirmed on `mistralai/mistral-small-4-119b-2603` over HTTP; findings + locked framework facts in [07 §3.1](docs/solution/07-implementation-plan.md). Two LLM-config fixes: `config/llm_config.hocon` form-B raw id + `config/custom_llm_info.hocon` alias→key pattern (`use_model_name` is alias-to-key, not raw id). Coded-tool `function.parameters` types are `string|int|float|boolean|array|object` (not `integer`/`number`) or the network is silently skipped at load. Invoker surface = `SimpleOneShot(connection_type="http")` → `processor.get_structure()/get_sly_data()`.

**Done + pushed (2026-07-08, `bbf0071`):** **0.3** `samples/python-payments-service` (Flask+pytest, auth module = Demo-2 SQLi plant site, real smoke test; 6 tests). **A1** coded tools `git_diff` + `ast_analyzer` + `dependency_graph` — **stdlib `ast`+`git`, zero new deps** (dodges tree-sitter/radon Windows friction; JS/TS deferred with node repo). Cooperate via `sly_data["change_profile_wip"]`; `dependency_graph` finalizes → validated `change_profile`. 4 golden-diff tests. `pytest.ini` scopes to `tests/`.

**Done, not yet committed (2026-07-08):** **A2** `secret_scanner` (secrets always Critical, entropy heuristic, added-lines only) + `dependency_cve` (offline OSV snapshot `config/osv_snapshot.json`, no network). **A3** `complexity_metrics` (stdlib approx-McCabe base-vs-head delta — no radon dep). **I/O convention aligned to 04 §5:** change-analysis tools take `args:{}`, read `event`+`repo_workspace` from Gateway-seeded sly_data via new `lib.workspace.run_inputs` (args still override for unit tests); A1 refactored to match. `dependency_graph` now merges LLM `classification`/`added_flags` (enum-guarded). Main suite **21 passing**. Still zero new runtime deps.

**Next (critical path, [07](docs/solution/07-implementation-plan.md)):**
1. **A7** — `contract_store` (sly_data producer-gap fix §9.1, no DB) + `report_publisher` (persists review_report; needed for M2) → then `decision_logger`/`cicd_action`/`notification` (M3/B4). `contract_store`+`report_publisher` first.
2. Network slices **B1→B4** (`registries/sentinel.hocon` + `registries/manifest.hocon`) → **M2** review report headless → **M3** both demo runs.

Runner note: the studio nsflow GUI is wired to NIM for manual testing (`neuro-san-studio/config/llm_config.hocon` local override; revert with `git -C neuro-san-studio checkout config/llm_config.hocon`).

## Conventions

- Diagrams: Mermaid in fenced blocks (GitHub/VS Code renderable). Avoid raw `{}` in flowchart labels.
- Section cross-references between docs use the form `[04-lld.md §5](04-lld.md)` — verify the target section exists after renumbering.
