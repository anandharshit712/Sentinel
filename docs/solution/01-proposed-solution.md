# Sentinel — Proposed Solution Specification

**Author:** Harshit Anand

**Project:** Sentinel (Multi-Agent Code Review + Smart Test Selection + Explainable Promotion Gating)
**Framework:** Neuro-SAN (sole multi-agent orchestrator)
**Context:** Cognizant Internal Hackathon
**Document role:** Master solution specification. This document is the single source of truth for _what the system is and how it works_. The DFD ([02-dfd.md](02-dfd.md)), HLD ([03-hld.md](03-hld.md)), LLD ([04-lld.md](04-lld.md)) and Architecture Diagram ([05-architecture-diagram.md](05-architecture-diagram.md)) are all derived from this document and must not contradict it.

---

## 1. Problem Statements (What We Are Solving)

Every code change travels the same road — **review → test → promote** — and each checkpoint is broken in its own way:

| #   | Problem                                               | Core failure                                                                                                                                    |
| --- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | **Code review is a slow, inconsistent bottleneck**    | Multi-expert review (logic, security, compliance) takes days; quality depends on which human reviews it; nobody holds full cross-domain context |
| P2  | **Test suites don't scale with codebases**            | Full suite runs on every change (30–90+ min); CI cost grows with repo size; flaky/slow CI breeds "just re-run it" culture                       |
| P3  | **Promotion is a black-box, all-or-nothing decision** | Binary pass/fail gating ignores unequal failure risk, deploy context (timing, incidents, batch size), and produces no reasoning trail           |
| P4  | **The checkpoints are disconnected (meta-problem)**   | A security concern raised in review never influences which tests run or whether a deploy proceeds; each gate re-derives context from scratch    |

## 2. Solution Statement

**One Neuro-SAN agent network acting as a connected intelligence layer across the delivery lifecycle**, where the output of every stage becomes a risk signal for the next:

1. **Multi-Agent Code Review (first pass)** — specialized Security and Code Quality agents review every change in seconds and produce structured, severity-ranked findings. Human reviewers are freed for architecture and business logic.
2. **Smart Test Selection** — the system reasons over _what actually changed_ (diff + dependency graph + test-to-source mapping) and runs only the relevant subset of the project's existing test suite, plus an always-on smoke safety net.
3. **Explainable Promotion Gating** — review findings, test results, change profile and environment context converge into one deterministic risk score; a Promotion Gating Agent applies a graduated **trust ladder** and outputs _Promote / Hold / Escalate_ with a complete, human-readable reasoning trail.

**The differentiator is cross-stage signal flow:** a Critical security finding raised during review directly raises the promotion risk score and can force human escalation _even when every test passes_ — automatically, with the reasoning visible. Solving P4 is what makes P1–P3 solutions compound instead of remaining three disconnected tools.

## 3. Design Principles (Binding Constraints on All Design Documents)

| #   | Principle                                    | Concrete meaning                                                                                                                                                                                                                                                                                                                  |
| --- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **Augment, don't replace**                   | The system is an intelligence stage plugged into existing CI/CD (GitHub Actions). It is not a CI engine, not a replacement for human reviewers. Teams keep their tooling.                                                                                                                                     |
| D2  | **LLM reasons, code decides**                | Every decision that must be auditable or safe is made by deterministic coded tools: the risk score is a fixed weighted formula, the trust ladder is a policy engine, test execution is a subprocess wrapper. LLM agents interpret, explain, contextualize, and handle edge cases — and may only _escalate_ risk, never reduce it. |
| D3  | **Language & framework agnostic — honestly** | The system detects each project's own tooling from manifest files (`requirements.txt`/`pyproject.toml`, `package.json`, `pom.xml`, `go.mod`, …) and invokes the **project's own test commands**, reasoning over the output. It never reimplements a test framework.                                                               |
| D4  | **Security by default**                      | Repo credentials, raw workspaces and structured stage payloads travel in Neuro-SAN's `sly_data` channel — invisible to LLMs and, by default, never leaving the agent network. Only explicitly allow-listed keys return to the caller.                                                                                             |
| D5  | **Graduated trust ladder**                   | Full automation only where mistakes are cheap (dev→test). Mandatory human approval where they are expensive (staging→production — _always_, hard-coded, regardless of score). Thresholds are configuration, so automation can widen as trust builds.                                                                              |
| D6  | **Declarative first**                        | The entire agent network is HOCON configuration (Neuro-SAN registries). Adding, removing, or re-modeling a review specialist is a config edit + coded-tool drop-in, not a platform change.                                                                                                                                        |
| D7  | **Deterministic contracts between stages**   | Every stage emits a versioned, JSON-schema'd payload (`schema_version` field on every contract). Stages consume contracts, not prose. This is what makes the network testable, replayable, and safely extensible.                                                                                                                 |

## 4. System Overview — Three Planes

The solution consists of exactly three cooperating planes. Everything in every design document belongs to one of them.

```mermaid
flowchart LR
    subgraph IP["1 — Integration Plane"]
        GH["GitHub Actions"] --- GW
        GW["Delivery Gateway<br/>(FastAPI service)"]
    end
    subgraph INTP["2 — Intelligence Plane"]
        NS["Neuro-SAN Server<br/>agent network:<br/>sentinel"]
    end
    subgraph DP["3 — Data & Presentation Plane"]
        PG[("PostgreSQL<br/>Risk History Store")]
        DB["Decision Dashboard"]
        NF["NSFlow<br/>(agent visualization)"]
    end
    GW -->|"canonical DeliveryEvent<br/>(HTTP streaming chat)"| NS
    NS -->|"structured decision payload"| GW
    NS --- PG
    GW --- PG
    DB --- GW
    NF --- NS
```

| Plane               | Component                                 | Responsibility                                                                                                                                                                                                                     | Technology                                 |
| ------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Integration         | **Delivery Gateway**                      | Receives webhooks from all three CI/CD platforms, verifies signatures, normalizes payloads into one canonical `DeliveryEvent`, invokes the agent network, exposes the dashboard/approval REST API, executes approved CI/CD actions | Python FastAPI                             |
| Intelligence        | **`sentinel` agent network** | All reasoning and decisioning: change analysis, security review, quality review, synthesis, test selection, test execution, environment context, risk scoring, promotion gating                                                    | Neuro-SAN (HOCON) + coded tools (Python)   |
| Data & Presentation | **PostgreSQL**                            | Runs, findings, reports, test results, risk scores, decisions, approvals, incidents, audit trail                                                                                                                                   | PostgreSQL 16                              |
| Data & Presentation | **Decision Dashboard**                    | Review reports, reasoning trails, pending-approval queue with Approve/Reject actions, audit views                                                                                                                                  | Served by Gateway (REST + SSE + static UI) |
| Data & Presentation | **NSFlow**                                | Live agent-network visualization for demo/debugging (stock Neuro-SAN client)                                                                                                                                                       | nsflow                                     |

**Why a Gateway exists (and why it is thin):** Neuro-SAN's server exposes a chat-style API. CI/CD platforms speak three different webhook dialects and expect three different callback APIs. The Gateway is the _only_ component that knows platform dialects; the agent network sees exactly one canonical event shape. This keeps the network portable across all three platforms with zero HOCON changes (principle D1, D7). The Gateway contains **no intelligence** — no scoring, no selection, no review logic. All of that lives in the Neuro-SAN network.

## 5. The Agent Network — `sentinel`

Neuro-SAN loads the network from `registries/sentinel.hocon` (registered in `registries/manifest.hocon`). One frontman; specialist LLM agents — change analysis, an **adaptive security-review fan-out** (a deterministic `review_planner` sizes the review and invokes **1–4** parallel `security_reviewer_*` agents, followed by a `senior_security_agent` summarizer), code quality, test selection, environment context, risk scoring, promotion gating; and nineteen deterministic coded tools. (Review synthesis is not an LLM agent — the deterministic `report_publisher` tool merges and scores findings; see §5.3.)

### 5.1 Network topology

```mermaid
flowchart TD
    CLIENT["Delivery Gateway (client)"] -->|"streaming_chat + sly_data"| FM

    FM["🎙️ delivery_coordinator<br/>(Frontman — LLM)"]

    FM --> CA["🧩 change_analysis_agent"]
    FM --> RP["🗺️ review_planner_tool<br/>(CodedTool — sizes the review)"]
    FM --> SR1["🔒 security_reviewer_1"]
    FM -.->|"1–4 (adaptive)"| SRN["🔒 security_reviewer_2..4"]
    FM --> SS["🕵️ senior_security_agent"]
    FM --> QR["✅ code_quality_agent"]
    FM --> RPUB["📋 report_publisher_tool<br/>(CodedTool — merge + score)"]
    FM --> TS["🎯 test_selection_agent"]
    FM --> TE["⚙️ test_runner_tool<br/>(CodedTool — no LLM)"]
    FM --> EC["📜 environment_context_agent"]
    FM --> RK["📊 risk_scoring_agent"]
    FM --> PG["🚦 promotion_gating_agent"]

    CA --> T1["git_diff_tool"]
    CA --> T2["ast_analyzer_tool"]
    CA --> T3["dependency_graph_tool"]

    SR1 --> T4["secret_scanner_tool"]
    SR1 --> T5["dependency_cve_tool"]
    SRN --> T4

    SS --> T17["review_digest_tool"]

    QR --> T6["complexity_metrics_tool"]

    TS --> T8["test_mapper_tool"]

    EC --> T9["incident_history_tool"]
    EC --> T10["deploy_window_tool"]

    SR1 --> T16["contract_store_tool"]
    SRN --> T16
    SS --> T16
    QR --> T16
    TS --> T16
    EC --> T16

    RK --> T11["risk_calculator_tool"]

    PG --> T12["trust_ladder_tool"]
    PG --> T13["decision_logger_tool"]
    PG --> T14["cicd_action_tool"]
    PG --> T15["notification_tool"]

    CA -.->|"change_profile (sly_data)"| TS
    RP -.->|"review_plan (sly_data)"| SR1
    SR1 -.->|"security_findings_shard_n (sly_data)"| RPUB
    SRN -.->|"security_findings_shard_n (sly_data)"| SS
    SS -.->|"senior_summary (sly_data)"| RPUB
    QR -.->|"quality_findings (sly_data)"| RPUB
    RPUB -.->|"review_report (sly_data)"| RK
    TE -.->|"test_results (sly_data)"| RK
    EC -.->|"env_context (sly_data)"| RK
    RK -.->|"risk_score (sly_data)"| PG
```

Solid arrows = Neuro-SAN `tools` references (who may call whom). Dotted arrows = data contracts flowing through the `sly_data` bulletin board (§5.4). The security review is an **adaptive fan-out**: `review_planner` partitions the reviewable surface into 1–4 shards and returns the exact reviewer agents to invoke; the frontman calls them one at a time (parallel batch is the post-hackathon optimization — §5.2). Review synthesis is deterministic (`report_publisher`), not an LLM agent (§5.4 framework constraint).

**Naming convention:** coded-tool labels in diagrams/tables use module names; the HOCON tool `name` drops the `_tool` suffix — module `coded_tools/sentinel/test_runner_tool.py` (class `TestRunnerTool`) is registered as tool `test_runner`. Authoritative mapping: [LLD §3 and §5](04-lld.md).

### 5.2 Orchestration model

- The **frontman** (`delivery_coordinator`) is the single client-facing agent. Its instructions encode an **explicit numbered pipeline** (Neuro-SAN best practice for multi-step orchestration): 1) change analysis → 2) **review planning** (deterministic `review_planner` sizes the security review) → 3) **security-review fan-out** (the 1–4 shard reviewers named by the planner, invoked one at a time) → 4) senior security summary → 5) code-quality review → 6) synthesis (deterministic `report_publisher`) → 7) test selection → 8) test execution → 9) environment context → 10) risk scoring → 11) gating → 12) final structured response.
- The step-3 shard reviewers are **independent by contract**. The design intent is a parallel batch (LLM parallel tool-calling); the hackathon frontman invokes them **strictly sequentially** (one at a time, waiting for each) because mistral's tool-call discipline degrades on the longer chain — parallelism is a post-hackathon latency optimization, never a correctness dependency (the merge is deterministic in code). Every step is one-at-a-time. See the framework-forced deviation in [07 §14](07-implementation-plan.md).
- **Ordering safety net (framework-forced):** because a long chain can still drop or reorder a step, `report_publisher` (#5) recomputes the deterministic detection floor (secrets + dangerous sinks over the whole change) itself when it synthesizes — so the critical/high deterministic findings are in the report **regardless of whether the LLM reviewers ran in time**. The reviewers still add their LLM-judged findings; the deterministic minimum is guaranteed (rule D2).
- Specialist agents follow the AAOSA interaction pattern (`aaosa_basic.hocon` substitutions) for delegation semantics, with rich one-line `function.description` entries — descriptions are the routing layer.
- **Test execution is deliberately not an LLM agent.** `test_runner_tool` is a plain `CodedTool` attached to the frontman: running tests is a deterministic act with no judgment in it (principle D2). Judgment (which tests) lives before it; interpretation (what results mean) lives after it.

### 5.3 Agent-by-agent specification

Full HOCON, instructions text, and function schemas are in the LLD ([04-lld.md](04-lld.md) §3). This section defines each agent's _contractual behavior_.

#### 1. 🎙️ `delivery_coordinator` — Frontman

|          |                                                                                                                                                                                                                                                                             |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Type     | LLM agent (frontman — only agent exposed to clients)                                                                                                                                                                                                                        |
| LLM      | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                             |
| Tools    | All specialist agents (change analysis, the 1–4 `security_reviewer_*`, `senior_security_agent`, code quality, test selection, environment context, risk scoring, promotion gating) + the coded tools `review_planner_tool`, `report_publisher_tool`, `test_runner_tool`      |
| Input    | Canonical `DeliveryEvent` JSON in the chat message; secrets/workspace in `sly_data`                                                                                                                                                                                         |
| Output   | Final structured run summary (text) + allow-listed `sly_data` payload to client: `run_id`, `change_profile`, `review_plan`, `review_report`, `test_plan`, `test_results`, `env_context`, `risk_score`, `decision`                                                           |
| Behavior | Validates the event, executes the 12-step pipeline (§5.2) strictly one tool call at a time, never skips risk scoring or gating, never invents stage outputs — if a stage fails it records the failure and routes to gating with a `stage_failure` flag (which the risk formula treats as elevated risk, fail-safe). |

#### 2. 🧩 `change_analysis_agent`

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| CodedTools | `git_diff_tool`, `ast_analyzer_tool`, `dependency_graph_tool`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Consumes   | `event` (sly_data)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Produces   | **`change_profile`** contract → sly_data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Behavior   | Obtains the diff, extracts changed files/functions via tree-sitter AST, builds the import-level dependency graph, computes **blast radius** (reverse reachability from changed modules), classifies the change (`feature / bug_fix / refactor / config / docs / mixed`), and sets **sensitive-area flags** by matching changed paths & symbols against the sensitive-area ruleset (auth, payments, data-deletion, migrations, public API surfaces). The LLM's role: classify ambiguous changes, name the blast radius in human terms; all raw facts come from the tools. |

#### 3. 🔒 Security review — adaptive fan-out (`review_planner` → `security_reviewer_1..4` → `senior_security_agent`)

Security review is a **deterministic-planner + adaptive-fan-out** subsystem, not a single agent. A normal PR diff stays on one reviewer; a large surface (full-repo **audit mode**, §12.1) spreads across up to four parallel reviewers — without ever letting an LLM decide how much compute to spend (D2).

**`review_planner_tool`** (CodedTool, no LLM) runs first. It applies exclusion globs (vendored/generated code) to the change surface, scores each added line for **hotspot** risk (dangerous-sink patterns, sensitive-area paths, entropy), counts hotspot lines `H`, and sizes the review: `shard_count = clamp(ceil(H / review_budget_lines), 1, max_review_shards=4)`. It partitions files into that many semantic shards (grouped by top-level module, balanced by hotspot weight), writes the **`review_plan`** contract to sly_data, and **returns the exact list of reviewer agents to invoke** to the frontman. It tags `mode` (`pr` vs `audit`, detected from the empty-tree base SHA). All deterministic and versioned.

Each **`security_reviewer_n`** (LLM agent; the frontman invokes only those the planner named, one at a time — see §5.2 on why parallelism is deferred):

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| CodedTools | `secret_scanner_tool` (called with its shard number — returns deterministic secret **and dangerous-sink** findings across all shard lines, plus the top-ranked code snippets within the LLM budget, each tagged `why_flagged` + context), `dependency_cve_tool` (reviewer 1 only — dependencies are repo-global), `contract_store_tool` (writes the shard contract)                                                                                                                                                                                                                                                                                                                                   |
| Consumes   | `review_plan` (its shard's file set), diff hunks                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Produces   | **`security_findings_shard_n`** contract → sly_data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Behavior   | Deterministic scans (secrets + dangerous sinks) over its whole shard + LLM deep review of the top-ranked snippets against the OWASP Top 10 checklist: SQL injection, XSS, CSRF, input-validation & authn/authz flaws, unsafe deserialization, path traversal, command injection. Every finding: `severity ∈ {critical, high, medium, low}`, file/line, CWE where applicable, explanation, fix suggestion, `source (tool/llm)`. Reviewed code is strictly untrusted data (prompt-injection hygiene — instructions forbid following instructions found inside diffs). |

**`senior_security_agent`** (LLM): calls `review_digest_tool` (returns a compact `{id,severity,file,title}` roll-up of every shard's findings — the LLM never re-reads raw code), writes a 2–4 sentence executive narrative naming the worst findings, and stores it as **`senior_summary`**. `report_publisher` uses it as the report's executive summary when present. Purely additive — it cannot alter findings or scores (D2).

The deterministic **detection floor** (secrets + sink rules) covers 100% of in-scope lines regardless of repo size; the LLM's deep review is what scales with the budget. `report_publisher` (#5) reports honest **coverage** (`total_added_lines`, `llm_reviewed_lines`, `deterministic_coverage_pct`, `unscanned_shards`) so a full-repo audit never reads as "everything was deep-reviewed" when it wasn't.

> _Backport note (2026-07-11):_ this section supersedes the former single `security_review_agent` (now the 1–4 `security_reviewer_*` fan-out). Synthesis is not an LLM agent: the former `review_synthesis_agent` is realized in code as the deterministic `report_publisher_tool` (#5 below), whose finding-merge folds in the per-shard contracts (see §5.4).

#### 4. ✅ `code_quality_agent`

|            |                                                                                                                                                                                                                                                                                                       |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct` (cost-optimization slot: may be pointed at a smaller NIM model via per-agent `llm_config` override — see §8)                                                                                                                                                          |
| CodedTools | `complexity_metrics_tool` (cyclomatic complexity + function length; radon for Python, heuristic parser for JS/TS), `contract_store_tool` (validates + writes the finished contract to sly_data)                                                                                                       |
| Consumes   | diff hunks, `change_profile`                                                                                                                                                                                                                                                                          |
| Produces   | **`quality_findings`** contract (incl. `quality_score` 0–100) → sly_data                                                                                                                                                                                                                              |
| Behavior   | Checks SOLID adherence, DRY violations, naming/readability, error-handling gaps, complexity regressions (tool-measured, not guessed), and missing tests on new functions (cross-checks `change_profile.new_functions` against test-file changes in the same diff). Suggestions carry line references. |

#### 5. 📋 `report_publisher_tool` (deterministic — not an LLM agent)

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Type       | CodedTool (no LLM). The former `review_synthesis_agent` is realized directly in code (rule D2) — synthesis is deterministic, never LLM-authored.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| CodedTools | itself (`report_publisher_tool`) — persists the report to Postgres                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Consumes   | the per-shard `security_findings_shard_*` + `quality_findings` + `senior_summary` contracts (from sly_data), `change_profile`                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Produces   | **`review_report`** contract → sly_data; developer-facing report published                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Behavior   | Recomputes the deterministic secret+sink floor, merges + de-duplicates overlapping findings (same file/line/root cause), rank-orders by severity, uses `senior_summary` as the executive summary when present, computes the **PR health score (0–100)** by the deterministic deduction rubric (critical −25, high −10, medium −4, low −1, floor 0 — see LLD §3; not LLM vibes), emits honest `coverage`, and issues a recommendation: ✅ Approve / ⚠️ Approve with changes / ❌ Request changes. The frontman invokes it as pipeline step 6 (§5.2), and _the same structured object_ feeds risk scoring (P4's fix, by construction). |

#### 6. 🎯 `test_selection_agent`

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| CodedTools | `test_mapper_tool`, `contract_store_tool` (validates + writes the finished `test_plan` to sly_data — required: `test_runner` reads it from there)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Consumes   | `change_profile`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Produces   | **`test_plan`** contract → sly_data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Behavior   | `test_mapper_tool` deterministically maps tests→sources using, in priority order: (a) coverage map if the repo has one (coverage.py / Istanbul JSON), (b) import-graph edges from test files, (c) naming & directory conventions (`test_x.py ↔ x.py`, `x.test.js ↔ x.js`). Selection = tests covering changed files ∪ tests covering downstream dependents (blast radius) ∪ **always-run smoke set** (configurable list per repo, safety net). Sensitive-area flags force _conservative expansion_ (widen to the owning module's full test directory). The LLM documents inclusion/exclusion reasoning and resolves ambiguous mappings — it may only **add** tests to the deterministic selection, never remove (D2). Output includes `selection_confidence ∈ {high, medium, low}`; low confidence is itself a risk signal. |

#### 7. ⚙️ `test_runner` — CodedTool (not an agent; module `test_runner_tool.py`)

|          |                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Type     | `CodedTool` (`neuro_san.interfaces.coded_tool.CodedTool`, `async_invoke`) attached directly to the frontman                                                                                                                                                                                                                                                                                                                                                                   |
| Consumes | `test_plan`, workspace (sly_data)                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Produces | **`test_results`** contract → sly_data                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Behavior | Detects the runner from manifests (`pyproject.toml`/`requirements.txt`+`pytest.ini` → pytest; `package.json` → jest/npm test; extensible detection table in LLD §5.9), executes the selected subset via runner-native selectors (pytest node IDs / `jest --testPathPatterns`), enforces a hard timeout, parses JUnit-XML/JSON output into structured pass/fail/skip + stack traces + timings + coverage delta (when the repo is coverage-instrumented). Zero LLM involvement. |

#### 8. 📜 `environment_context_agent`

|            |                                                                                                                                                                                                                                                     |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                     |
| CodedTools | `incident_history_tool` (Postgres: incidents/reverts for repo+target env in lookback window), `deploy_window_tool` (freeze calendar & risky-window policy: e.g. Friday ≥16:00 local, weekends, release freezes), `contract_store_tool` (validates + writes the finished contract to sly_data)       |
| Consumes   | `event.target_transition`                                                                                                                                                                                                                           |
| Produces   | **`env_context`** contract → sly_data                                                                                                                                                                                                               |
| Behavior   | Emits per-environment risk context: recent incident count/recency, deploy-timing risk flag, environment stability marker, change batch size (commits in this promotion). All facts tool-sourced; LLM summarizes into flags + one-paragraph context. |

#### 9. 📊 `risk_scoring_agent` — the convergence point

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| CodedTools | `risk_calculator_tool`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Consumes   | `review_report`, `test_results`, `change_profile`, `env_context`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Produces   | **`risk_score`** contract → sly_data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Behavior   | Assembles the four upstream contracts into a `risk_input`, calls `risk_calculator_tool`, which applies the **fixed, versioned weighted formula** (§6) and returns score 0–100 + per-factor contribution breakdown. The LLM then (a) writes the structured explanation naming every contributing factor, (b) may flag an anomaly the formula missed and **raise** the score with logged justification — it can never lower it (D2; this is the prompt-injection firewall for gating). A Critical security finding alone pushes the score into escalation territory _even if all tests passed_ — the cross-stage core of the system. |

#### 10. 🚦 `promotion_gating_agent` — final decision maker

|            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM        | `nvidia-llama-3.3-70b-instruct`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| CodedTools | `trust_ladder_tool`, `decision_logger_tool`, `cicd_action_tool`, `notification_tool`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Consumes   | `risk_score`, `event.target_transition`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Produces   | **`decision`** contract → sly_data (allow-listed back to the Gateway); decision row + full reasoning trail persisted; CI/CD action or notification fired                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Behavior   | `trust_ladder_tool` evaluates the policy file (§7) → `promote / hold / escalate`. **Hard-coded floor in the tool itself (not just policy): staging→production never auto-promotes.** The LLM composes the human-readable reasoning trail: what review found, what was tested and why, what passed/failed, which context factors weighed in, and why this decision follows. `decision_logger_tool` persists everything; `cicd_action_tool` executes promotion (or no-ops in simulated mode); `notification_tool` notifies (Slack/Teams webhook + dashboard queue) on hold/escalate. |

### 5.4 `sly_data` — the secure bulletin board

Neuro-SAN's `sly_data` dictionary is invisible to LLMs and by default never crosses network boundaries. The solution uses it for two purposes:

1. **Secrets & bulk data:** `git_token`, `repo_workspace` (server-side clone path), raw diffs — never enter any prompt except the specific hunks a review agent must see.
2. **Stage contracts:** every producer writes its versioned contract under its reserved key; every consumer reads its required inputs. Coded tools share state without round-tripping large JSON through LLM context (cost + reliability + no leakage).

**Framework constraint:** `sly_data` is writable only by coded tools (it is invisible to LLMs). Agents whose contract is finalized by an existing coded tool write through that tool (`dependency_graph` → `change_profile`, `review_planner` → `review_plan`, `report_publisher` → `review_report`, `risk_calculator` → `risk_score`, `decision_logger` → `decision`). The agents without such a tool (the `security_reviewer_*` shard reviewers, `senior_security_agent`, `code_quality`, `test_selection`, `environment_context`) call the generic **`contract_store`** coded tool as their mandatory final step — it validates the payload against the contract's JSON schema and writes it to the reserved sly_data key. `report_publisher` then merges the per-shard `security_findings_shard_*` contracts + `quality_findings` into the single `review_report` (deterministic dedup + scoring — rule D2; this replaces the never-built `review_synthesis_agent`).

| Key                           | Producer                  | Consumers                                             |
| ----------------------------- | ------------------------- | ----------------------------------------------------- |
| `event`                       | Gateway (client request)  | all                                                   |
| `git_token`, `repo_workspace` | Gateway                   | git/test tools only                                   |
| `change_profile`              | change_analysis_agent     | test_selection, security/quality review, risk_scoring |
| `review_plan`                 | review_planner_tool       | security_reviewer_*, report_publisher, Gateway (upstream) |
| `security_findings_shard_n`   | security_reviewer_n (via `contract_store`) | report_publisher (merge)             |
| `senior_summary`              | senior_security_agent (via `contract_store`) | report_publisher                    |
| `quality_findings`            | code_quality_agent (via `contract_store`) | report_publisher                     |
| `review_report`               | report_publisher_tool     | risk_scoring, Gateway (upstream)                      |
| `test_plan`                   | test_selection_agent (via `contract_store`) | test_runner_tool                                      |
| `test_results`                | test_runner_tool          | risk_scoring, Gateway (upstream)                      |
| `env_context`                 | environment_context_agent (via `contract_store`) | risk_scoring                                          |
| `risk_input`, `risk_score`    | risk_scoring_agent        | promotion_gating, Gateway (upstream)                  |
| `decision`                    | promotion_gating_agent    | Gateway (upstream)                                    |
| `run_id`                      | Gateway                   | all tools (correlation)                               |

Frontman `allow.to_upstream.sly_data = ["run_id", "change_profile", "review_plan", "review_report", "test_plan", "test_results", "env_context", "risk_score", "decision"]` — the only data that leaves the network (the per-shard `security_findings_shard_*` stay in-network; their content reaches the Gateway already merged inside `review_report`). Tokens and workspaces never do.

## 6. Risk Scoring Model (Deterministic, Versioned)

`risk_calculator_tool` implements formula version `risk-v1`. Score = `min(100, Σ contributions)`; every contribution is reported line-item in the explanation.

| Factor group           | Rule                                               | Points (cap)                                        |
| ---------------------- | -------------------------------------------------- | --------------------------------------------------- |
| Security findings      | per **Critical**                                   | +40 (cap 80)                                        |
|                        | per **High**                                       | +15 (cap 45)                                        |
|                        | per **Medium**                                     | +5 (cap 20)                                         |
|                        | per **Low**                                        | +1 (cap 5)                                          |
| Quality                | `(100 − pr_health_score) × 0.15`                   | ≤15                                                 |
| Test results           | any selected-test failure                          | +25 base, +10 per additional failing suite (cap 45) |
|                        | smoke-set failure                                  | +40                                                 |
|                        | `selection_confidence = low`                       | +10                                                 |
|                        | stage failure / tests could not run                | +30                                                 |
| Change profile         | per sensitive-area flag                            | +15 (cap 30)                                        |
|                        | blast radius > 20 dependent modules                | +10 (>50: +20)                                      |
|                        | change size > 500 changed LOC                      | +5 (>2000: +10)                                     |
| Environment context    | incident on repo+env in last 7 days                | +15                                                 |
|                        | deploy-window violation (e.g. Friday-evening prod) | +10                                                 |
|                        | target env currently unstable                      | +10                                                 |
|                        | oversized promotion batch (policy threshold)       | +5                                                  |
| LLM anomaly escalation | risk_scoring_agent flags an un-modeled anomaly     | raise-only, logged with justification               |

**Bands:** 0–24 **low** · 25–49 **medium** · 50–74 **high** · 75–100 **critical**.

Properties: monotonic (nothing subtracts — absence of problems is the reward), explainable line-by-line, versioned (`formula_version` stored with every score so historical decisions remain interpretable), and tunable per organization via the weights file (`risk_weights_v1.yaml`, LLD §5) without code changes.

_Worked check (Demo Run 2):_ 1 Critical SQLi finding (+40) + 1 Critical hardcoded-secret finding (+40, within the 80 cap) + auth sensitive-area flag (+15) + all tests green (0) = **95 (critical)** — the trust ladder escalates on every transition at critical band, even though CI is green. Binary gating structurally cannot do this.

## 7. Trust Ladder (Policy-as-Configuration)

`trust_ladder_tool` evaluates `config/trust_ladder_policy.yaml` (mounted config; defaults below). Policy can tighten but never loosen the hard floor.

| Transition           | low (0–24)                                                                                      | medium (25–49)                | high (50–74)     | critical (75–100)                  |
| -------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------- | ---------------- | ---------------------------------- |
| dev → test           | ✅ auto-promote                                                                                 | ✅ auto-promote               | ✅ auto-promote  | ⏸️ hold + notify (≥90 🆘 escalate) |
| test → qa            | ✅ auto-promote                                                                                 | ⏸️ hold + notify              | ⏸️ hold + notify | 🆘 escalate                        |
| qa → staging         | ✅ auto-promote                                                                                 | 🆘 recommend + human approval | 🆘 escalate      | 🆘 escalate                        |
| staging → production | 🆘 **always** recommend + mandatory human approval — hard-coded in tool, policy cannot override | ← same                        | ← same           | ← same                             |

Decisions: **promote** (Gateway fires `cicd_action_tool` promotion), **hold** (block + notify author/team; re-runnable after fixes), **escalate** (approval request in dashboard queue + Slack/Teams; a human Approve/Reject via the dashboard resolves it, and the resolution is logged with approver identity).

Why this ladder: fully autonomous production deployment is both a hard sell and a genuine risk. Automation only where mistakes are cheap makes the system pilotable, explainable to leadership, and thresholds are data — relaxable later as trust builds (D5).

## 8. LLM Strategy — NVIDIA NIM Primary, Provider-Agnostic by Construction

- **Primary provider: NVIDIA NIM** via Neuro-SAN's built-in `nvidia` provider class (`langchain-nvidia-ai-endpoints`, `NVIDIA_API_KEY`). Hackathon: hosted NIM endpoints (`build.nvidia.com` / `integrate.api.nvidia.com`). Production option: **self-hosted NIM containers in-cluster** — client code never leaves company infrastructure, a decisive enterprise-adoption argument.
- **Model assignments:** default network-level model `nvidia-llama-3.3-70b-instruct` (registered in Neuro-SAN's default LLM info; reliable function-calling — mandatory, since AAOSA delegation is function-calling). Per-agent `llm_config` overrides are the designed cost-optimization slots for lighter agents (`code_quality_agent`, `environment_context_agent`): a smaller NIM model (e.g. `llama-3.1-8b-instruct`) is added via the `AGENT_LLM_INFO_FILE` extension mechanism (LLD §6.2). `nvidia-deepseek-r1` is _not_ used by default (weak tool-calling; reasoning-only leaf experiments at most).
- **Fallback chain** (Neuro-SAN `fallbacks` list, tried in order; providers without keys are culled automatically):
  1. `nvidia-llama-3.3-70b-instruct` (NIM)
  2. `${?FALLBACK_MODEL_NAME}` — optional env-injected second provider (e.g. `gpt-4o` or `claude-sonnet`) with no HOCON edit
- **Central config:** one `config/llm_config.hocon` included by the network file; `${?MODEL_NAME}` env override switches the whole network's model at deploy time.

## 9. Repository Tooling Detection (D3 in Practice)

| Detected manifest                                                | Language | Test runner invoked | Subset selector                             | Result format parsed                                                |
| ---------------------------------------------------------------- | -------- | ------------------- | ------------------------------------------- | ------------------------------------------------------------------- |
| `pyproject.toml` / `requirements.txt` (+ `pytest.ini`/`tox.ini`) | Python   | `pytest`            | pytest node IDs / `-k`                      | JUnit XML (`--junitxml`)                                            |
| `package.json` (jest present)                                    | JS/TS    | `npx jest`          | `--testPathPatterns` / `--findRelatedTests` | jest JSON (`--json`)                                                |
| `package.json` (script `test`, no jest)                          | JS/TS    | `npm test`          | pattern arg passthrough                     | JUnit XML via reporter if configured, else exit-code + stdout parse |
| `pom.xml`                                                        | Java     | `mvn test`          | `-Dtest=` class list                        | Surefire XML                                                        |
| `go.mod`                                                         | Go       | `go test`           | `-run` regex + package paths                | `-json` stream                                                      |

Hackathon MVP proves the first two rows live (Python + Node sample services); the table is the extension contract for the rest. Detection and parsing live entirely in `test_runner_tool` / `test_mapper_tool` — adding a language touches only those two coded tools.

Jest flag note: `--testPathPatterns` (plural) exists in Jest 30+; Jest ≤29 uses `--testPathPattern`. `test_runner_tool` detects the installed Jest major from the lockfile and picks the flag; the sample repo pins Jest 30.

## 10. Data Contracts (Summary)

Every contract carries `schema_version`, `run_id`, `produced_by`, `produced_at`. Full JSON Schemas: LLD §4.

| Contract                     | Key fields                                                                                                                                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DeliveryEvent`              | `event_id`, `source (github/manual)`, `repo{url,name,default_branch}`, `change{base_sha,head_sha,branch,pr_id?,title,description,author}`, `target_transition{from_env,to_env}`, `requested_by` |
| `ChangeProfile`              | `files[{path,language,change_type,hunks,functions_changed[]}]`, `new_functions[]`, `classification`, `loc_added/removed`, `blast_radius{direct[],transitive[],count}`, `sensitive_flags[]`                     |
| `Finding` (security/quality) | `id`, `category`, `severity`, `file`, `line_start/end`, `cwe?`, `title`, `explanation`, `fix_suggestion`, `source (tool/llm)`                                                                                  |
| `ReviewPlan`                 | `mode (pr/audit)`, `budget_lines`, `shards[{shard,label,files[],hotspot_weight}]`, `metrics{files_scanned,excluded_files,added_lines,hotspot_lines,shard_count,basis}`                                          |
| `ReviewReport`               | `executive_summary`, `findings[] (deduped, ranked)`, `pr_health_score`, `recommendation (approve/approve_with_changes/request_changes)`, `coverage?{total_added_lines,llm_reviewed_lines,deterministic_coverage_pct,shards,unscanned_shards}` |
| `SeniorSummary`              | `summary` (executive narrative from `senior_security_agent`; becomes `ReviewReport.executive_summary` when present)                                                                                            |
| `TestPlan`                   | `selected[{test_id,reason,mapping_source}]`, `smoke_set[]`, `excluded_summary`, `selection_confidence`, `estimated_runtime`                                                                                    |
| `TestResults`                | `runner`, `command`, `totals{passed,failed,skipped}`, `cases[{test_id,status,duration,failure_message?,stack?}]`, `coverage_delta?`, `duration`, `timed_out`                                                   |
| `EnvContext`                 | `target_env`, `incidents{count,most_recent_at}`, `deploy_window{risky,reason}`, `env_stability`, `batch_size`, `flags[]`                                                                                       |
| `RiskScore`                  | `score`, `band`, `formula_version`, `contributions[{factor,points,evidence_ref}]`, `llm_escalation?{points_added,justification}`, `explanation`                                                                |
| `Decision`                   | `decision (promote/hold/escalate)`, `transition`, `policy_version`, `reasoning_trail`, `actions_taken[]`, `approval_required`, `approval_status?`                                                              |

## 11. End-to-End Data Flow

1. A GitHub Action (on PR / promotion) or the `/api/v1/simulate` endpoint posts a `DeliveryEvent` → **Gateway** authenticates the caller (bearer token), creates the `run` row (Postgres), prepares the workspace (shallow clone at `head_sha`), and returns the `run_id`.
2. Gateway invokes Neuro-SAN `streaming_chat` on `sentinel` with the event JSON as the message and `{event, run_id, git_token, repo_workspace}` in `sly_data`; streamed agent progress is relayed live to the dashboard (SSE) and persisted.
3. **change_analysis_agent** → `change_profile`.
4. **review_planner_tool** sizes the review → 1–4 **security_reviewer** agents + **senior_security_agent** + **code_quality_agent** (invoked one at a time, §5.2) → findings.
5. **report_publisher_tool** (deterministic) merges + scores the findings → `review_report`, published to the developer (dashboard) _in seconds_ — the review deliverable is done here even before tests run.
6. **test_selection_agent** → `test_plan` (deterministic core + LLM-documented reasoning).
7. **test_runner_tool** executes the plan with the repo's own runner → `test_results`.
8. **environment_context_agent** → `env_context` (ran in parallel since step 3).
9. **risk_scoring_agent** → deterministic score + explanation, cross-referencing all four upstream contracts.
10. **promotion_gating_agent** → trust-ladder decision + full reasoning trail; persists; **promote** → the CI check passes (the GitHub Action gate); **hold/escalate** → notifications + dashboard approval queue.
11. Human approves/rejects escalations in the dashboard; resolution executes or blocks the promotion and is appended to the audit trail.
12. Every artifact of steps 1–11 is queryable afterward: "why was this promoted?" always has a concrete answer.

## 12. CI/CD Platform Integration (GitHub Actions)

The Gateway exposes one HTTP ingest endpoint (`POST /api/v1/simulate`) that accepts a `DeliveryEvent`, clones `repo.url` server-side, runs the network, and returns the run. The **GitHub Action** (`.github/workflows/sentinel-gate.yml`) is the live integration; the same endpoint powers demo/simulate mode. Full endpoint/payload detail: LLD §7. (The single Gateway adapter interface — D6 — leaves room for other CI platforms post-hackathon; none are specified here.)

|                  | GitHub Actions                                                                                                                             |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Inbound trigger  | `.github/workflows/sentinel-gate.yml` runs on `pull_request` / `workflow_dispatch`, builds a `DeliveryEvent`, and `POST`s it to `/api/v1/simulate` (bearer token `SENTINEL_TOKEN`) |
| Blocking usage   | The Action polls the run and **fails the check unless `decision == promote`**                                                              |
| Promotion action | Check passes → the PR is mergeable (the gate is the Action job itself)                                                                     |
| Review comment   | Dashboard (posting the review back as a PR comment is post-hackathon)                                                                      |
| Demo mode        | `/api/v1/simulate` builds a `DeliveryEvent` and clones `repo.url` server-side — identical code path, no live platform needed              |

### 12.1 Audit mode (full-repo, integration-free)

Sentinel normally reviews a **diff** (`base_sha..head_sha`). **Audit mode** points the same pipeline at an **entire repository** with no CI integration and no prepared diff — the standalone "run it on any public repo" path.

- **Mechanism (zero new pipeline code):** the change surface is diffed against the git **empty-tree** object (`4b825dc642cb6eb9a060e54bf8d69288fbee4904`), so every file reads as fully added. `git_diff_tool` / `ast_analyzer_tool` / `dependency_graph_tool` handle this unchanged.
- **Entry points:** `scripts/run_repo.py --full <url>` (localhost helper), or a `/api/v1/simulate` `DeliveryEvent` whose `change.base_sha` is the empty-tree hash. `review_planner` detects the empty-tree base and tags `review_plan.mode = "audit"`.
- **Adaptive cost:** the whole-repo surface is exactly what the fan-out (§5.3 #3) exists for — `review_planner` sizes 1–4 reviewers by hotspot volume so a medium repo is reviewed in parallel without an unbounded single-agent context.
- **The decision is _advisory_ in audit mode.** The risk formula (§6) is change-churn-driven, so treating a whole repo as one change pegs churn factors high and the band toward `critical` — correct for a "safe to promote _this change_?" question, meaningless as a repo-health verdict. Consumers read the **review findings + honest coverage** (§5.3 #3), not the promote/hold/escalate verdict. The helper prints an explicit `AUDIT MODE … decision is advisory` banner.
- **Coverage honesty:** the deterministic detection floor (secrets + dangerous-sink rules) covers 100% of in-scope lines regardless of repo size; LLM deep-review scales with the per-agent budget and shard count; `report_publisher` reports what was deep-reviewed vs scanned so audit output never over-claims.

## 13. Persistence — Risk History Store (PostgreSQL)

PostgreSQL 16 everywhere (hackathon docker-compose service and production managed instance — same engine, zero divergence). Owned by the Gateway (migrations) and written by coded tools via a shared thin DAO. Full DDL: LLD §8.

Tables: `runs`, `review_reports`, `findings`, `test_plans`, `test_results`, `env_contexts`, `risk_scores`, `decisions`, `approvals`, `incidents` (seedable for demo; feeds `incident_history_tool`), `outcomes` (generic `decision_id + outcome_type + payload JSONB` — records post-decision facts such as reverts/incidents against past decisions), `audit_events` (append-only).

## 14. Decision Dashboard

Served by the Gateway (REST + SSE + static single-page UI). Screens (API mapping: LLD §9; full frontend design: [06-frontend-design.md](06-frontend-design.md)):

1. **Runs** — list/filter by repo, band, decision, transition.
2. **Run detail** — the money screen: review report (findings ranked, health score), test plan with selection reasoning, test results, risk-score factor breakdown, decision + full reasoning trail, live agent progress while running (SSE). Demo Runs 1 & 2 are shown side-by-side from here.
3. **Approvals** — pending escalations queue; Approve/Reject with comment; approver identity recorded; action executes the promotion or blocks it.
4. **Audit** — append-only event log per run/decision.

AuthN/Z: hackathon = static bearer token; production = OIDC (company SSO) with roles `viewer / approver / admin` (HLD §7).
**NSFlow** remains the agent-network visualization (which agent is talking to which, live) — used in the demo alongside the dashboard; it is stock Neuro-SAN, port 4173.

## 15. Deployment Model (Summary — HLD Owns Detail)

|                       | Hackathon (demo)                                                                   | Production (company-wide)                                                                                                                            |
| --------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Topology              | `docker-compose`: `neuro-san-server`, `gateway` (+dashboard), `postgres`, `nsflow` | Kubernetes (cloud-agnostic): Deployments for neuro-san server & Gateway (HPA), managed PostgreSQL, Ingress, Secrets via K8s Secrets/External Secrets |
| LLM                   | NIM hosted API                                                                     | NIM hosted **or self-hosted NIM in-cluster** (GPU pool) — code-privacy mode                                                                          |
| Test execution        | subprocess inside a dedicated runner container (resource-limited)                  | ephemeral, sandboxed **K8s Job per test run** (no network egress except package cache; CPU/mem/time-limited)                                         |
| Same images both ways | ✅ compose is a subset of the K8s deployment — no demo/production fork             |

## 16. Security Model (Summary — HLD §7 Owns Detail)

- Webhook authenticity: HMAC/token verification per platform at the Gateway; replay-window enforcement.
- Secrets: `sly_data` for in-network transport (D4); K8s Secrets at rest; never logged (structured-log redaction filter on known-secret keys + secret-scanner patterns applied to own logs).
- LLM safety: reviewed code is untrusted input — review agents' instructions explicitly refuse in-code instructions; the gating path is deterministic (trust ladder tool), and the LLM can only _raise_ risk (§6) — a prompt-injected diff cannot talk the system into auto-promoting.
- Isolation: test execution sandboxed (container/K8s Job, no secrets mounted beyond repo read token, egress-restricted).
- Auditability: every decision, approval, and automated action lands in append-only `audit_events` with actor identity (human or agent).
- Data retention: workspaces are ephemeral (deleted post-run); stored artifacts are structured findings/reports, retention-configurable.

## 17. Scalability & Company-Wide Adoption

- **Stateless intelligence plane:** Neuro-SAN server pods and Gateway pods are horizontally scalable (HPA); per-run state lives in `sly_data` (in-request) and Postgres (durable). Concurrent runs = concurrent chat sessions — no shared mutable state.
- **Per-team onboarding = configuration:** point the team's webhook at the Gateway, add a repo config entry (smoke set, sensitive-area paths, policy overrides). No code changes, no pipeline migration (D1).
- **Cost control:** selective testing cuts CI compute; per-agent model right-sizing (§8) cuts LLM spend; run concurrency and test-runner quotas are capped per namespace.
- **Adoption path (trust ladder as rollout strategy):** pilot team → dev→test automation only → widen transitions as thresholds prove out → org-wide. Each step is a policy-file change, not a rollout.
- **Extensibility by design (D6, D7):** new review specialist = one HOCON agent block + optional coded tool; new language = detection-table rows in two coded tools; new CI platform = one Gateway adapter; risk-formula evolution = new `formula_version` weights file. The versioned contracts and generic `outcomes` table mean future capabilities plug into the same spine without schema breaks.

## 18. Hackathon MVP Scope

1. ✅ Working Neuro-SAN network — all components (§5.3) configured in `registries/sentinel.hocon`, 19 coded tools implemented (incl. `review_planner`, `review_digest`).
2. ✅ Sample multi-language repos: `samples/python-payments-service` (Flask + pytest), `samples/node-catalog-service` (Express + Jest) — proving manifest-driven language agnosticism.
3. ✅ **Demo Run 1 — happy path:** small low-risk change → clean parallel review → small relevant test subset selected & passing → low score → auto-promote (dev→test) with full reasoning trail on the dashboard.
4. ✅ **Demo Run 2 — the escalation (money shot):** change touching the auth module with planted string-concatenated SQL **and a hardcoded secret** → Security Review flags **two Criticals** → risk 95 (≥75, critical band; §6 worked check) **although every test passes** → gating escalates to human approval, reasoning trail explicitly citing both security findings; approver resolves it live in the dashboard.
5. ✅ Side-by-side reasoning trails (dashboard) + live agent choreography (NSFlow).
6. CI/CD platform scope: **GitHub Actions (via `/api/v1/simulate`) + simulate mode** (§12). The single Gateway adapter interface (D6) leaves room for other platforms post-hackathon; none are specified here.
7. Stretch: live GitHub webhook (not simulated); seeded `incidents` row visibly shifting a repeat run's score; Slack/Teams notification on the approval step.
8. Post-M4 enhancement (built): **audit mode** (§12.1) + **adaptive security-review fan-out** (§5.3 #3) — `scripts/run_repo.py --full` runs the whole pipeline over any public repo, `review_planner` sizing 1–4 parallel reviewers by hotspot volume, with honest deep-review coverage reporting.

## 19. Success Metrics

| Metric                          | Current state                      | Directional goal                                       |
| ------------------------------- | ---------------------------------- | ------------------------------------------------------ |
| First-pass review turnaround    | Days                               | Seconds–minutes                                        |
| CI test runtime per commit      | Full suite (30–90 min large repos) | Relevant subset + smoke set                            |
| CI compute spend                | Baseline                           | Meaningful reduction via selective execution           |
| Promotion decision transparency | Binary pass/fail, no reasoning     | Full explainable reasoning trail, queryable            |
| Human reviewer focus            | Everything incl. basics            | Architecture, business logic, mentorship               |
| Review → deployment signal flow | None                               | Review findings directly influence gating (Demo Run 2) |

## 20. Traceability — Problem → Solution Component

| Problem                | Solved by                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------- |
| P1 review bottleneck   | §5.3 agents 2–5 (parallel specialist review + synthesis in seconds)                               |
| P2 test-suite scale    | §5.3 agents 6–7 (deterministic selection + native execution) + §9                                 |
| P3 black-box promotion | §6 risk model + §7 trust ladder + reasoning trail + dashboard                                     |
| P4 disconnected gates  | §5.4 contract flow: `review_report` → `risk_score` → `decision` (one network, one bulletin board) |
