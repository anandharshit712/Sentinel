# Sentinel — Data Flow Diagrams (DFD)

**Derived from:** [01-proposed-solution.md](01-proposed-solution.md) (authoritative). Companion documents: [HLD](03-hld.md), [LLD](04-lld.md), [Architecture](05-architecture-diagram.md).
**Levels:** L0 (context) → L1 (system decomposition) → L2 (drill-down of processes 2, 3, 4 and 5).

## 0. Notation

Mermaid cannot draw strict Gane–Sarson symbols; the mapping used throughout:

| DFD element     | Rendered as   | Naming                                  |
| --------------- | ------------- | --------------------------------------- |
| External entity | Rectangle     | `E# Name`                               |
| Process         | Rounded node  | `P# Name` (L2: `P#.# Name`)             |
| Data store      | Cylinder      | `D# Name`                               |
| Data flow       | Labeled arrow | flow name from the Data Dictionary (§5) |

Every named flow and store is defined in the **Data Dictionary** (§5). Contract field detail lives in [01 §10](01-proposed-solution.md) and full JSON Schemas in [LLD §4](04-lld.md).
Coded-tool names in _italics_ are module names; HOCON tool names drop the `_tool` suffix (mapping: [LLD §3/§5](04-lld.md)).

---

## 1. Level 0 — Context Diagram

The system boundary is the **Sentinel** (Gateway + Neuro-SAN `sentinel` network + Risk History Store + Dashboard). Everything else is external.

```mermaid
flowchart LR
    E1["E1 Developer"]
    E2["E2 CI/CD Platform<br/>(GitHub Actions / Jenkins / GitLab CI)"]
    E3["E3 Release Manager / Approver"]
    E4["E4 Git Hosting Service<br/>(repo content)"]
    E5["E5 NVIDIA NIM LLM Endpoint<br/>(hosted or self-hosted)"]
    E6["E6 OSV.dev CVE Database"]
    E7["E7 Notification Channel<br/>(Slack / Teams webhook)"]

    S(("P0<br/>AI Delivery<br/>Intelligence Layer"))

    E1 -- "code change (push / PR)" --> E2
    E2 -- "F1 webhook event<br/>(PR / stage transition)" --> S
    S -- "F14 promotion action /<br/>gate status" --> E2
    S -- "F13 review report comment" --> E2
    E4 -- "F2 repo content (clone/diff)" --> S
    S -- "LLM prompts" --> E5
    E5 -- "LLM completions" --> S
    S -- "manifest package queries" --> E6
    E6 -- "known-CVE records" --> S
    S -- "F15 hold / escalation notification" --> E7
    E7 -- "notification delivery" --> E3
    E1 -- "view run, review report" --> S
    E3 -- "F16 approve / reject (+comment)" --> S
    S -- "reasoning trails, dashboards" --> E1
    S -- "approval queue, reasoning trails" --> E3
```

**External entity register**

| ID  | Entity                     | Direction | Interface (detail in LLD §7)                                        |
| --- | -------------------------- | --------- | ------------------------------------------------------------------- |
| E1  | Developer                  | in/out    | Dashboard UI (HTTPS), PR comments via platform                      |
| E2  | CI/CD platform             | in/out    | Webhooks in (HMAC/token-verified); status/dispatch/trigger APIs out |
| E3  | Release manager / approver | in/out    | Dashboard approval queue; notified via E7                           |
| E4  | Git hosting                | in        | `git clone` / diff fetch with scoped read token                     |
| E5  | NVIDIA NIM                 | in/out    | OpenAI-compatible chat completions (`nvidia` provider class)        |
| E6  | OSV.dev                    | in        | REST batch query; offline snapshot fallback (demo mode)             |
| E7  | Slack/Teams                | out       | Incoming-webhook POST                                               |

---

## 2. Level 1 — System Decomposition

Processes P1–P7; stores D1–D4. P2–P5 run inside the Neuro-SAN agent network; P1, P6, P7 run in the Delivery Gateway.

```mermaid
flowchart TD
    E2["E2 CI/CD Platform"]
    E4["E4 Git Hosting"]
    E1["E1 Developer"]
    E3["E3 Approver"]
    E7["E7 Slack / Teams"]

    P1("P1 Ingest & Normalize<br/>Delivery Event<br/><i>[Gateway]</i>")
    P2("P2 Analyze Change<br/><i>[change_analysis_agent]</i>")
    P3("P3 Review Code<br/><i>[security + quality + synthesis]</i>")
    P4("P4 Select & Execute Tests<br/><i>[test_selection_agent + test_runner_tool]</i>")
    P5("P5 Score Risk & Gate Promotion<br/><i>[env_context + risk_scoring + promotion_gating]</i>")
    P6("P6 Resolve Human Approval<br/><i>[Gateway + Dashboard]</i>")
    P7("P7 Execute CI/CD Action<br/>& Publish Reports<br/><i>[Gateway adapters]</i>")

    D1[("D1 Risk History Store<br/>(PostgreSQL)")]
    D2[("D2 Run Workspace<br/>(ephemeral clone)")]
    D3[("D3 Policy & Config Store<br/>(trust ladder, weights,<br/>repo config, sensitive rules)")]
    D4[("D4 Audit Log<br/>(append-only tables)")]

    E2 -- "F1 webhook event" --> P1
    P1 -- "F2 clone @ head_sha" --> E4
    E4 -- "repo content" --> D2
    P1 -- "F3 DeliveryEvent + run_id<br/>(streaming_chat + sly_data)" --> P2
    P1 -- "run row" --> D1

    P2 -- "reads workspace" --> D2
    P2 -- "F4 change_profile" --> P3
    P2 -- "F4 change_profile" --> P4
    P2 -- "F4 change_profile" --> P5

    P3 -- "reads diff hunks" --> D2
    P3 -- "F7 review_report" --> P5
    P3 -- "F7 review_report" --> D1
    P3 -- "F13 report for publication" --> P7

    P4 -- "executes tests in" --> D2
    P4 -- "F9 test_results" --> P5
    P4 -- "F8 test_plan / F9 results" --> D1

    D3 -- "policies, weights, windows" --> P5
    D1 -- "incident history" --> P5
    P5 -- "F11 risk_score + F12 decision" --> D1
    P5 -- "decision events" --> D4
    P5 -- "F12 decision (promote)" --> P7
    P5 -- "F15 hold/escalate notice" --> E7
    P5 -- "escalation item" --> P6

    E3 -- "F16 approve / reject" --> P6
    P6 -- "approval record" --> D1
    P6 -- "approval events" --> D4
    P6 -- "approved promotion" --> P7

    P7 -- "F14 status / dispatch / trigger" --> E2
    P7 -- "F13 PR / MR comment" --> E2
    P7 -- "action results" --> D4

    D1 -- "runs, reports, trails" --> E1
    D1 -- "approval queue, trails" --> E3
```

**Cross-stage signal flow (the differentiator) is visible here as a pure data-flow fact:** `F7 review_report` produced by P3 is a _direct input_ to P5 — review findings mechanically raise the promotion risk score with no human relay (solves P4 of [01 §1](01-proposed-solution.md)).

---

## 3. Level 2 Drill-Downs

### 3.1 P2 — Analyze Change

```mermaid
flowchart TD
    IN["F3 DeliveryEvent<br/>(base_sha, head_sha)"] --> P21
    D2[("D2 Run Workspace")]

    P21("P2.1 Extract Diff<br/><i>git_diff_tool</i>")
    P22("P2.2 Parse ASTs of Changed Files<br/><i>ast_analyzer_tool (tree-sitter)</i>")
    P23("P2.3 Build Dependency Graph<br/>& Blast Radius<br/><i>dependency_graph_tool</i>")
    P24("P2.4 Classify Change &<br/>Flag Sensitive Areas<br/><i>LLM + sensitive-area ruleset</i>")

    D2 --> P21
    P21 -- "F2a unified diff +<br/>changed file list" --> P22
    P21 -- "F2a diff" --> P24
    P22 -- "F2b changed functions/classes<br/>+ new_functions" --> P23
    P22 -- "F2b symbols" --> P24
    P23 -- "F2c dependent modules,<br/>blast radius count" --> P24
    D3[("D3 sensitive-area ruleset<br/>(repo config)")] --> P24
    P24 -- "F4 change_profile<br/>(schema: 01 §10)" --> OUT["→ sly_data bulletin board<br/>(consumed by P3, P4, P5)"]
```

| Sub-process | Deterministic core                                                       | LLM role                                                                                |
| ----------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| P2.1        | `git diff base..head` on D2 workspace; rename/binary detection           | none                                                                                    |
| P2.2        | tree-sitter parse per changed file; function/class span mapping to hunks | none                                                                                    |
| P2.3        | import-graph construction; reverse reachability from changed modules     | none                                                                                    |
| P2.4        | path/symbol match against sensitive-area rules                           | classify `feature/bug_fix/refactor/config/docs/mixed`; name blast radius in human terms |

### 3.2 P3 — Review Code

```mermaid
flowchart TD
    F4["F4 change_profile"] --> P31
    F4 --> P32
    D2[("D2 Run Workspace<br/>(diff hunks)")]

    subgraph PAR["parallel"]
        P31("P3.1 Security Review<br/><i>security_review_agent</i>")
        P32("P3.2 Quality Review<br/><i>code_quality_agent</i>")
    end

    P311("P3.1a Scan Secrets<br/><i>secret_scanner_tool</i>")
    P312("P3.1b Check Dependency CVEs<br/><i>dependency_cve_tool</i>")
    P321("P3.2a Measure Complexity<br/><i>complexity_metrics_tool</i>")

    E6["E6 OSV.dev"]

    D2 --> P31
    D2 --> P32
    P31 --> P311
    P31 --> P312
    P312 <--> E6
    P32 --> P321

    P311 -- "secret hits" --> P31
    P312 -- "CVE findings" --> P31
    P321 -- "complexity metrics" --> P32

    P31 -- "F5 security_findings<br/>(severity, CWE, line, fix)" --> P33
    P32 -- "F6 quality_findings<br/>(+ quality_score)" --> P33

    P33("P3.3 Synthesize Review<br/><i>review_synthesis_agent</i>")
    P33 -- "F7 review_report<br/>(deduped, ranked, health score,<br/>recommendation)" --> OUT1["→ sly_data (consumed by P5)"]
    P33 -- "F7 persist" --> D1[("D1 Risk History Store")]
    P33 -- "F13 publish request" --> P7["P7 (PR/MR comment)"]
```

Dedup rule in P3.3: findings sharing `(file, overlapping line range, root cause category)` merge, keeping highest severity and both explanations (detail: LLD §3, `review_synthesis_agent` instructions).

### 3.3 P4 — Select & Execute Tests

```mermaid
flowchart TD
    F4["F4 change_profile"] --> P41
    D2[("D2 Run Workspace")]
    D3[("D3 repo config<br/>(smoke set, timeouts)")]

    P41("P4.1 Map Tests to Sources<br/><i>test_mapper_tool</i>")
    P42("P4.2 Compose Test Plan<br/><i>test_selection_agent (LLM,<br/>add-only on deterministic core)</i>")
    P43("P4.3 Detect Runner & Execute<br/><i>test_runner_tool</i>")
    P44("P4.4 Parse Results<br/><i>test_runner_tool</i>")

    D2 -- "test files, imports,<br/>coverage map if present" --> P41
    P41 -- "F8a test↔source map<br/>(+ mapping_source per edge)" --> P42
    D3 -- "smoke set" --> P42
    P42 -- "F8 test_plan<br/>(selected + smoke + reasoning<br/>+ selection_confidence)" --> P43
    D2 -- "manifests<br/>(pyproject/package.json/…)" --> P43
    P43 -- "runner-native subset command<br/>(pytest node IDs / jest patterns)" --> P44
    P44 -- "F9 test_results<br/>(pass/fail/skip, traces, timing,<br/>coverage_delta)" --> OUT["→ sly_data (consumed by P5)"]
    P42 -- "F8 persist" --> D1[("D1")]
    P44 -- "F9 persist" --> D1
```

Selection set algebra (P4.2): `selected = covering(changed_files) ∪ covering(blast_radius) ∪ smoke_set`, conservatively widened when `change_profile.sensitive_flags ≠ ∅`; the LLM may only add tests and must justify every exclusion summary line (D2 principle, [01 §5.3](01-proposed-solution.md)).

### 3.4 P5 — Score Risk & Gate Promotion

```mermaid
flowchart TD
    F3["F3 event.target_transition"] --> P51
    D1[("D1 Risk History Store")]
    D3[("D3 Policy & Config Store")]
    D4[("D4 Audit Log")]

    P51("P5.1 Gather Environment Context<br/><i>environment_context_agent</i>")
    P52("P5.2 Compute Risk Score<br/><i>risk_calculator_tool (formula risk-v1)</i>")
    P53("P5.3 Explain & Sanity-Check<br/><i>risk_scoring_agent (raise-only)</i>")
    P54("P5.4 Apply Trust Ladder<br/><i>trust_ladder_tool (policy engine)</i>")
    P55("P5.5 Compose Reasoning Trail<br/>& Log Decision<br/><i>promotion_gating_agent + decision_logger_tool</i>")

    D1 -- "incident history<br/>(repo + env, 7d window)" --> P51
    D3 -- "deploy windows,<br/>freeze calendar" --> P51
    P51 -- "F10 env_context" --> P52

    F7["F7 review_report"] --> P52
    F9["F9 test_results"] --> P52
    F4["F4 change_profile"] --> P52
    D3 -- "weights file (risk-v1)" --> P52

    P52 -- "score + factor contributions" --> P53
    P53 -- "F11 risk_score<br/>(score, band, contributions,<br/>optional raise-only escalation)" --> P54
    D3 -- "trust_ladder_policy.yaml" --> P54
    P54 -- "promote | hold | escalate<br/>(prod floor hard-coded)" --> P55

    P55 -- "F12 decision + reasoning trail" --> D1
    P55 -- "decision event" --> D4
    P55 -- "F12 (promote) → P7" --> P7["P7 Execute CI/CD Action"]
    P55 -- "F15 notify (hold/escalate)" --> E7["E7 Slack/Teams"]
    P55 -- "escalation item" --> P6["P6 Resolve Human Approval"]
```

### 3.5 P1 / P6 / P7 — Gateway Processes

```mermaid
flowchart TD
    E2["E2 CI/CD Platform"]
    E3["E3 Approver"]
    E4["E4 Git Hosting"]

    P11("P1.1 Verify Webhook<br/>(HMAC-SHA256 / token, replay window)")
    P12("P1.2 Normalize to DeliveryEvent<br/>(platform adapter, inbound)")
    P13("P1.3 Create Run + Prepare Workspace<br/>(shallow clone @ head_sha)")
    P14("P1.4 Invoke Agent Network<br/>POST /api/v1/sentinel/streaming_chat<br/>sly_data: event, run_id, git_token, repo_workspace")
    P15("P1.5 Relay Progress<br/>(stream → SSE + persist)")

    P61("P6.1 Queue Escalation")
    P62("P6.2 Record Approve/Reject<br/>(approver identity + comment)")
    P63("P6.3 Trigger Resulting Action")

    P71("P7.1 Post Review Comment<br/>(PR / MR / build description)")
    P72("P7.2 Set Gate Status<br/>(check / commit status)")
    P73("P7.3 Dispatch Promotion<br/>(workflow_dispatch / buildWithParameters / trigger pipeline)")

    D1[("D1")]
    D2[("D2")]
    D4[("D4")]
    NS["P2–P5<br/>(Neuro-SAN network)"]
    DB["Dashboard (SSE/REST)"]

    E2 -- "F1" --> P11 --> P12 --> P13 --> P14
    P13 -- "clone" --> E4
    P13 -- "workspace" --> D2
    P13 -- "run row" --> D1
    P14 -- "F3" --> NS
    NS -- "stream (AGENT_FRAMEWORK / AI msgs)" --> P15
    P15 --> DB
    P15 -- "progress events" --> D1

    NS -- "escalation" --> P61 --> DB
    E3 -- "F16" --> P62 --> D1
    P62 --> D4
    P62 --> P63 --> P73

    NS -- "F13" --> P71 -- "comment" --> E2
    NS -- "F12" --> P72 -- "status" --> E2
    P63 --> P73 -- "F14" --> E2
    P73 -- "action result" --> D4
```

---

## 4. Data Stores

| ID  | Store                 | Technology                                                  | Written by         | Read by                                | Content (tables: LLD §8)                                                                                                                             |
| --- | --------------------- | ----------------------------------------------------------- | ------------------ | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Risk History Store    | PostgreSQL 16                                               | P1, P3, P4, P5, P6 | P5 (incidents), Dashboard, all queries | `runs`, `review_reports`, `findings`, `test_plans`, `test_results`, `env_contexts`, `risk_scores`, `decisions`, `approvals`, `incidents`, `outcomes` |
| D2  | Run Workspace         | Ephemeral filesystem volume (per-run dir, deleted post-run) | P1.3               | P2, P3, P4                             | Shallow clone at `head_sha`; test artifacts                                                                                                          |
| D3  | Policy & Config Store | Mounted config files (K8s ConfigMap / repo `config/` dir)   | Operators (GitOps) | P2.4, P4.2, P5                         | `trust_ladder_policy.yaml`, `risk_weights_v1.yaml`, `repo_config.yaml` (smoke sets, sensitive-area rules, deploy windows)                            |
| D4  | Audit Log             | PostgreSQL `audit_events` (append-only)                     | P5, P6, P7         | Dashboard audit screen                 | Actor (human/agent), action, payload ref, timestamp                                                                                                  |

## 5. Data Dictionary (Flows)

| Flow | Name              | Composition (summary — full schema LLD §4)                                                                                                                                                                | Producer → Consumer                                          |
| ---- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ------- |
| F1   | Webhook event     | Platform-native PR/pipeline payload + signature header                                                                                                                                                    | E2 → P1                                                      |
| F2   | Repo content      | Shallow clone at `head_sha`; `F2a` unified diff + file list; `F2b` changed symbols; `F2c` dependents/blast radius                                                                                         | E4 → D2; internal P2                                         |
| F3   | DeliveryEvent     | `event_id, source, repo{...}, change{base_sha, head_sha, branch, pr_id?, title, description, author}, target_transition{from_env, to_env}, requested_by` + sly_data `{run_id, git_token, repo_workspace}` | P1 → P2/P5 (via streaming_chat)                              |
| F4   | change_profile    | `files[], new_functions[], classification, loc_added/removed, blast_radius{}, sensitive_flags[]`                                                                                                          | P2 → P3, P4, P5                                              |
| F5   | security_findings | `Finding[]`: `severity, category, file, line, cwe?, title, explanation, fix_suggestion, source`                                                                                                           | P3.1 → P3.3                                                  |
| F6   | quality_findings  | `Finding[]` + `quality_score`                                                                                                                                                                             | P3.2 → P3.3                                                  |
| F7   | review_report     | `executive_summary, findings[] (deduped/ranked), pr_health_score, recommendation`                                                                                                                         | P3.3 → P5, D1, P7                                            |
| F8   | test_plan         | `selected[{test_id, reason, mapping_source}], smoke_set[], excluded_summary, selection_confidence, estimated_runtime`; `F8a` raw test↔source map                                                          | P4.2 → P4.3, D1                                              |
| F9   | test_results      | `runner, command, totals{}, cases[], coverage_delta?, duration, timed_out`                                                                                                                                | P4.4 → P5, D1                                                |
| F10  | env_context       | `target_env, incidents{}, deploy_window{}, env_stability, batch_size, flags[]`                                                                                                                            | P5.1 → P5.2                                                  |
| F11  | risk_score        | `score, band, formula_version, contributions[], llm_escalation?, explanation`                                                                                                                             | P5.3 → P5.4, D1                                              |
| F12  | decision          | `decision, transition, policy_version, reasoning_trail, actions_taken[], approval_required, approval_status?`                                                                                             | P5.5 → D1, P6, P7; allow-listed to Gateway via `to_upstream` |
| F13  | review comment    | Rendered `review_report` (markdown)                                                                                                                                                                       | P7.1 → E2                                                    |
| F14  | promotion action  | Platform-specific: check status / `workflow_dispatch` / `buildWithParameters` / pipeline trigger                                                                                                          | P7.2–7.3 → E2                                                |
| F15  | notification      | Hold/escalate summary + dashboard deep-link                                                                                                                                                               | P5.5 → E7                                                    |
| F16  | approval          | `approve                                                                                                                                                                                                  | reject, comment, approver_identity`                          | E3 → P6 |

Note: flows F5, F6, F8, F10 are produced by LLM agents but land on the sly_data bulletin board via the `contract_store` coded tool ([LLD §5.17](04-lld.md)) — sly_data is writable only by coded tools ([01 §5.4](01-proposed-solution.md)).

**Invariants** (enforced by design, verifiable in audit):

1. No flow bypasses P5 to reach F14 — every platform action descends from a logged `decision` (or an approval resolving one).
2. `git_token` and `repo_workspace` appear in no flow crossing the system boundary (sly_data `to_upstream` allow-list excludes them).
3. F7 (review) is an input to F11 (risk) in every run — the cross-stage link is structural, not optional.
4. F12 with `transition = staging→production` never carries `decision = promote` without a matching F16 — the hard floor of the trust ladder.
