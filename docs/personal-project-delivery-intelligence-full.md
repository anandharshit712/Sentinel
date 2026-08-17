# 🧠 Autonomous Delivery Intelligence Platform

### Multi-Agent Code Review • Smart Test Selection • AI Test Generation with Quality Evaluation • Explainable Promotion Gating

### Built with Neuro-SAN | Personal Project (Post-Hackathon Evolution)

---

## 📌 Problem Statement

Every code change passes through the same lifecycle — **review → test → promote** — and each checkpoint is broken in a distinct way. Worse, the checkpoints are disconnected: signals discovered at one gate never inform the next.

### Problem 1: Code Review Is Slow, Inconsistent, and Siloed

A PR needs multiple experts (logic, security, performance, compliance) who work at different speeds across timezones. Review cycles stretch to days; quality varies by reviewer; no single human holds full cross-domain context; junior developers get inconsistent feedback that slows their growth.

### Problem 2: Test Suites Don't Scale With Codebases

Full-suite CI runs on every commit waste 30–90+ minutes and significant compute on tests irrelevant to the change. Engineers lose flow state, distrust flaky CI, and under-invest in new tests because the existing suite is already painful.

### Problem 3: Coverage Gaps Are Invisible Until It's Too Late

New functions ship untested under deadline pressure. Coverage tools say _that_ something is untested but don't help _write_ the missing test. Junior developers don't know what a good test looks like; legacy code with zero tests has no natural entry point.

### Problem 4: Promotion Is a Black-Box, All-or-Nothing Decision

Binary "all tests pass → promote" logic ignores that failures carry unequal risk, ignores context (deploy timing, incident history, change size), and leaves no reasoning trail for "why was this promoted/blocked?"

### Problem 5 (The Meta-Problem): Disconnected Gates

A security concern raised in review doesn't influence which tests run or whether a deploy proceeds. Each gate re-derives context from scratch. The lifecycle has no memory.

---

## 💡 Solution Overview

**A single Neuro-SAN agent network acting as a connected intelligence layer across the entire delivery lifecycle**, with four linked capabilities:

1. **Multi-Agent Code Review (First Pass)** — Security, Quality, Performance, and Compliance agents review every change in seconds, producing a unified, severity-ranked report. Human reviewers focus on architecture and business logic.

2. **Smart Test Selection** — Reasons about what changed (diff + dependency graph) and runs only the relevant subset of existing tests, plus a safety-net smoke set.

3. **AI-Assisted Test Generation with Quality Evaluation** — Identifies changed code lacking coverage, generates idiomatic tests for it, and — critically — **scores those tests via an independent evaluation pass grounded in mutation testing** before any human sees them. Only trustworthy tests are proposed; nothing is auto-committed.

4. **Explainable Promotion Gating** — Review findings, test results, coverage improvements, and environment context converge into one risk score; a Promotion Gating Agent applies a graduated trust ladder to produce promote / hold / escalate decisions with a full reasoning trail. Every decision is reviewable after the fact via a Promotion Review loop that feeds outcomes back into future scoring.

### Design Philosophy: Signals Flow Between Stages

The differentiator is cross-stage signal flow: a Critical security finding in review raises the promotion risk score; a closed coverage gap (via an approved generated test) _lowers_ it. No gate operates blind.

### Design Philosophy: Research-Grade on the Hard Part

Free from hackathon constraints, this project deliberately goes deep on the hardest open problem: **making AI-generated tests trustworthy**. Most "AI writes your tests" tools generate-and-hope; this project builds an evaluation layer with objective, mutation-testing-based ground truth — the difference between a gimmick and a genuinely useful tool, and the most publishable piece of the system.

### Design Philosophy: Language & Framework Agnostic via Native Tooling

The system detects each project's tooling via manifest files (`package.json`, `pom.xml`, `requirements.txt`, `go.mod`, `Cargo.toml`, etc.) and invokes the project's own test commands. Generated tests are written in the **idiomatic style and framework the project already uses** (pytest for a pytest repo, Jest for a Jest repo), detected from existing test files.

---

## 🏗️ Approach & Architecture

### Framework: Neuro-SAN (HOCON-based Agent Network)

---

### High-Level Agent Network Design

```
                    ┌──────────────────────────────┐
                    │   🔔 PR / Pipeline Trigger      │
                    │  (GitHub / Jenkins / GitLab)   │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │      🎙️ Frontman Agent          │
                    │   (Delivery Coordinator)        │
                    └──┬──────────┬────────────────┬─┘
                       │          │                  │
      ┌────────────────▼──┐  ┌────▼──────────────┐  │
      │ 🧩 Change          │  │ 🛡️ Review Agent     │  │
      │ Analysis Agent     │  │ Cluster:           │  │
      │ (diff + dep graph) │  │  🔒 Security        │  │
      └────────┬──────────┘  │  ✅ Quality         │  │
               │              │  ⚡ Performance     │  │
               │              │  📜 Compliance      │  │
               │              └────┬──────────────┘  │
               │                   │                  │
               │      ┌────────────▼────────────┐    │
               │      │ 📋 Review Synthesis Agent │    │
               │      └────────────┬────────────┘    │
               │                   │                  │
   ┌───────────▼─────────────┐     │      ┌──────────▼─────────────┐
   │ 📉 Coverage Gap Agent     │     │      │ 📜 Environment Context  │
   │ (untested changed code)  │     │      │ Agent (per-env risk)    │
   └───────┬──────────┬──────┘     │      └──────────┬─────────────┘
           │          │             │                  │
 ┌─────────▼───────┐ ┌▼───────────────────┐           │
 │ 🎯 Test Selection │ │ ✍️ Test Generation   │           │
 │ Agent (existing) │ │ Agent (new tests)   │           │
 └─────────┬───────┘ └─────────┬──────────┘           │
           │                    │                       │
           │         ┌──────────▼──────────┐           │
           │         │ 🧪 Test Quality       │           │
           │         │ Evaluation Agent     │           │
           │         │ (mutation-grounded)  │           │
           │         └──────────┬──────────┘           │
           │                    │                       │
           └──────────┬─────────┘                       │
                      │                                 │
         ┌────────────▼────────────┐                    │
         │ ⚙️ Test Execution         │                    │
         │ Interface (CodedTool)    │                    │
         └────────────┬────────────┘                    │
                      │                                 │
         ┌────────────▼─────────────────────────────────▼┐
         │            📊 Risk Scoring Agent                │
         │  (review findings + test results + coverage    │
         │   improvement + environment context)           │
         └────────────┬───────────────────────────────────┘
                      │
         ┌────────────▼────────────┐
         │ 🚦 Promotion Gating Agent │
         │  (trust ladder decision)  │
         └────────────┬────────────┘
                      │
     ┌────────────────┼────────────────────┐
     ▼                ▼                     ▼
✅ Auto-Promote  ⏸️ Hold + Notify   🆘 Escalate to Human
     │                │                     │
     └────────────────┴──────────┬──────────┘
                                 │
                    ┌────────────▼────────────┐
                    │ 🔁 Promotion Review Loop  │
                    │ (outcomes → Risk History  │
                    │  Store → future scoring)  │
                    └─────────────────────────┘
```

---

### Agent Breakdown

#### 1. 🎙️ Frontman Agent — _Delivery Coordinator_

- **Role:** Entry point for PRs and pipeline stage transitions; extracts metadata (repo, branch, commit SHA, PR description, languages, target environment) and delegates in parallel
- **LLM:** GPT-4o / Claude Sonnet

#### 2. 🧩 Change Analysis Agent

- **Role:** Determines what structurally changed
- **LLM:** GPT-4o
- **CodedTools:** Git diff parser, tree-sitter AST analysis (multi-language), dependency graph builder
- **Output:** Change profile — files/functions changed, blast radius, change classification, sensitive-area flags (auth, payments, data deletion, migrations, public APIs)

#### 3. 🛡️ Review Agent Cluster

Four specialists run in parallel on every change:

- **🔒 Security Review Agent** (GPT-4o) — OWASP Top 10 patterns, hardcoded secrets, CVEs in dependencies, input validation and auth flaws, unsafe deserialization, path traversal, command injection. Findings carry severity levels and **feed directly into Risk Scoring**.
- **✅ Code Quality Agent** (Claude Haiku / GPT-4o-mini) — SOLID adherence, DRY violations, naming/readability, cyclomatic complexity, error-handling gaps.
- **⚡ Performance Agent** (GPT-4o-mini) — algorithmic inefficiency (O(n²) where O(n) suffices), N+1 queries, memory/resource leaks, blocking I/O in async contexts, missing caching opportunities.
- **📜 Compliance & License Agent** (Claude Haiku) — open-source license compatibility (GPL/MIT/Apache conflicts), GDPR/HIPAA/PCI-DSS-relevant patterns (PII handling, data logging), deprecated API usage.

#### 4. 📋 Review Synthesis Agent

- **Role:** De-duplicates and prioritizes all cluster findings into one report: executive summary, severity-ranked issues with line references, PR health score (0–100), recommendation (✅ / ⚠️ / ❌)
- **LLM:** GPT-4o / Claude Sonnet
- Delivered to the developer in seconds **and** forwarded as structured input to Risk Scoring

#### 5. 📉 Coverage Gap Agent

- **Role:** Cross-references the change profile against existing coverage data to find exactly which changed functions/branches lack adequate tests
- **LLM:** GPT-4o-mini
- **How it works:** Ingests the project's own coverage reports (`coverage.py`, Istanbul/nyc, JaCoCo, `go test -cover`); classifies each changed function as fully / partially / uncovered; prioritizes gaps by risk (an uncovered function behind a sensitive-area flag outranks an uncovered utility)
- **Output:** Prioritized coverage gaps, routed to the Test Generation Agent

#### 6. 🎯 Test Selection Agent

- **Role:** Picks the relevant subset of _existing_ tests for this change
- **LLM:** GPT-4o-mini
- **How it works:** Deterministic dependency-graph mapping selects (a) tests covering changed files, (b) tests covering downstream dependents, (c) an always-run smoke set as safety net; the LLM reasons over edge cases and documents inclusions/exclusions. High-risk changes expand selection conservatively.
- **Output:** Minimal-but-sufficient execution plan with reasoning

#### 7. ✍️ Test Generation Agent

- **Role:** Writes new tests for the identified coverage gaps
- **LLM:** GPT-4o / Claude Sonnet (highest code-generation quality needed)
- **How it works:** For each prioritized gap, analyzes the function's signature, logic branches, and surrounding context; generates tests covering the happy path, boundary conditions, error/exception paths, and at least one adversarial edge case; writes them in the project's detected test framework and style conventions (naming patterns, assertion style, fixture/mocking conventions)
- **Guardrail:** Tests are produced as a **proposed diff — never auto-committed**; they pass through the Quality Evaluation Agent before any human sees them

#### 8. 🧪 Test Quality Evaluation Agent — _The Hardest, Most Valuable Part_

- **Role:** A skeptical, independent second opinion that scores generated tests before they're surfaced
- **LLM:** GPT-4o — deliberately a separate reasoning pass, not the call that generated the test
- **What it evaluates:**
  - **Behavioral relevance** — does the test verify intended behavior, or assert trivial/tautological things (a known LLM failure mode)?
  - **Implementation mirroring** — does the test just re-encode the function's logic instead of verifying behavior? (Diff-based critique comparing test against implementation)
  - **Mutation-grounded validity** — deliberately introduce plausible bugs into the function (flipped conditionals, off-by-ones via mutmut/Stryker/PIT) and confirm the generated test _catches_ them. This gives an objective, measurable quality signal rather than pure LLM judgment.
  - **Coverage contribution** — does it meaningfully increase branch/line coverage, or is it redundant?
- **Output:** A quality score per test; only tests above a configurable threshold reach the developer for review
- **Why this matters:** This agent is the difference between "AI test generation" as a liability and as a trustworthy tool — and it's the most differentiated, most publishable component of the entire platform

#### 9. ⚙️ Test Execution Interface (CodedTool)

- **Role:** Deterministically executes selected existing tests and approved generated tests via the project's native runner
- **Output:** Structured pass/fail/coverage results

#### 10. 📜 Environment Context Agent

- **Role:** Contextual risk signals for the target environment — incident history, deploy timing, environment stability, batch size
- **LLM:** Claude Haiku

#### 11. 📊 Risk Scoring Agent

- **Role:** The convergence point — synthesizes **review findings + test results + coverage improvement + change profile + environment context** into a weighted 0–100 risk score with a structured explanation
- **LLM:** GPT-4o
- **Key mechanics:** A Critical security finding can push the score into escalation territory even with green tests; conversely, coverage gaps closed by approved generated tests count as a **positive risk-reduction signal**

#### 12. 🚦 Promotion Gating Agent — _Final Decision Maker_

- **Role:** Applies the graduated trust ladder and produces the decision with full reasoning
- **LLM:** GPT-4o / Claude Sonnet
- **Trust ladder:**
  - **Dev → Test:** auto-promote unless risk is very high
  - **Test → QA:** auto-promote on low risk; hold on medium/high
  - **QA → Staging:** recommend + human approval above low risk
  - **Staging → Production:** always a recommendation + mandatory human approval, regardless of score
- **Output:** Promote / Hold / Escalate + a full reasoning trail: what the review found, which tests ran and why, which tests were generated with what quality scores, and which contextual factors were weighed

#### 13. 🔁 Promotion Review Loop (Risk & Quality History Store)

- **Role:** Closes the loop. Every decision and its downstream outcome is logged to Postgres:
  - Was a promotion later reverted? Did an incident follow?
  - Did an approved generated test later catch a real bug post-merge?
  - Were human overrides applied, and why?
- **Feedback effects:** Historical calibration refines risk thresholds; generated-test outcomes refine the Test Generation Agent's prompting strategy; override patterns surface where the trust ladder should tighten or relax
- This is what turns the platform from a static rules engine into a system that **gets better with use**

---

### Why the Test Quality Evaluation Agent Deserves the Deepest Iteration

- **Mutation testing as ground truth:** injected realistic bugs give an objective pass/fail signal for test quality — measurable, not vibes
- **Historical calibration:** tracking which generated tests catch real bugs post-merge feeds back into generation strategy
- **Diff-based critique:** explicitly flagging implementation mirroring, the sneakiest LLM test-writing failure mode
- Most existing tools skip evaluation entirely and dump tests on developers, eroding trust fast. A credible evaluation layer is a genuinely open problem — and the methodology is worth a standalone public writeup (blog post / portfolio piece), independent of the rest of the system.

---

### Technology Stack

| Component                    | Technology                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| Agent Orchestration          | Neuro-SAN (HOCON config)                                                                      |
| Agent Communication          | AAOSA Protocol                                                                                |
| LLM Providers                | OpenAI GPT-4o / GPT-4o-mini, Claude Sonnet/Haiku                                              |
| CI/CD Integration            | Jenkins, GitHub Actions, GitLab CI APIs (pluggable)                                           |
| Change Analysis              | Git, tree-sitter (multi-language AST), dependency graph builder                               |
| Security Tooling             | CVE/advisory checkers, secret-pattern scanners (as CodedTools)                                |
| Coverage Data                | coverage.py, Istanbul/nyc, JaCoCo, go test -cover (read existing reports)                     |
| Mutation Testing             | mutmut / Stryker / PIT (language-dependent)                                                   |
| Test Execution               | Native project runners via CodedTool subprocess wrapper                                       |
| Risk & Quality History Store | Postgres                                                                                      |
| UI                           | NSFlow + custom dashboard (review reports, generated-test review, promotion reasoning trails) |
| Secure Data Handling         | Neuro-SAN `sly_data` channel                                                                  |

---

### Data Flow (End to End)

1. Commit/PR triggers the pipeline; Frontman Agent receives the event
2. Change Analysis, the Review Agent Cluster, and Environment Context run in parallel
3. Review Synthesis delivers the developer-facing report in seconds
4. Coverage Gap Agent identifies untested changed code
5. In parallel: Test Selection picks relevant existing tests; Test Generation drafts tests for the gaps
6. Test Quality Evaluation scores generated tests (mutation-grounded); only high-quality ones proceed
7. Test Execution runs existing-selected + newly-approved tests via native tooling
8. Risk Scoring combines review findings, results, coverage improvement, and context
9. Promotion Gating applies the trust ladder → decision + full reasoning
10. Developer reviews generated tests via dashboard before they're merged into the suite — human stays in the loop for test adoption even when promotion itself is automated for low-risk environments
11. Outcomes flow into the Risk & Quality History Store, improving future decisions

---

## 🎯 Success Metrics (Personal Project Goals)

| Metric                            | Goal                                                          |
| --------------------------------- | ------------------------------------------------------------- |
| First-pass review turnaround      | Seconds, with severity-ranked findings                        |
| CI test runtime per commit        | Reduced via selective execution, measured on sample repos     |
| Coverage on actively changed code | Measurable increase over time via approved generated tests    |
| Generated-test trustworthiness    | High percentage of surfaced tests catch injected mutations    |
| Generated-test false positives    | Low — tests should rarely fail for trivial/flaky reasons      |
| Decision transparency             | Fully explainable reasoning for every promotion decision      |
| Learning effect                   | Demonstrable change in scoring behavior after logged outcomes |

---

## 🚀 Project Roadmap (Iterative)

### Phase 1 — Foundation (Hackathon Carry-Over)

- Core network: Frontman, Change Analysis, Security + Quality review agents, Review Synthesis, Test Selection, Test Execution, Risk Scoring, Promotion Gating
- End-to-end on a single Python sample repo

### Phase 2 — Full Review Cluster + Multi-Language

- Add Performance and Compliance agents to the review cluster
- Extend Change Analysis and Test Execution to 2–3 languages via manifest detection and tree-sitter
- Validate the language-agnostic claim on varied sample repos

### Phase 3 — Test Generation (The Interesting Part)

- Build Coverage Gap and Test Generation agents
- Basic Test Quality Evaluation via LLM critique
- Add mutation-testing-based evaluation as the objective second signal

### Phase 4 — Trust & Learning Loop

- Build the Risk & Quality History Store and Promotion Review loop
- Feedback loops: which generated tests caught real bugs, which promotions were reverted → refine prompting and thresholds

### Phase 5 — Polish & Showcase

- Clean dashboard for reasoning trails and generated-test review
- Public writeup of the mutation-testing evaluation methodology — genuinely novel enough for a blog post / portfolio piece
- Optional: trial the system against real open-source repos, or extract the evaluation layer as a standalone tool

---

## 💭 Why This Is a Strong Personal Project

- **Cross-disciplinary depth:** multi-agent orchestration (Neuro-SAN) + program analysis (AST/dependency graphs/coverage) + ML evaluation methodology (mutation testing as ground truth for generated content) — exactly the intersection an AI-engineering portfolio should demonstrate
- **A real open problem:** trustworthy AI-generated tests are unsolved in the tooling space; the evaluation layer is a legitimate contribution, not a wrapper
- **Incrementally demoable:** each phase produces something standalone, so the project has value even if later phases slip
- **Natural extensions:** real-world open-source validation, or publishing the evaluation approach as an independent tool
- **A coherent narrative:** hackathon MVP → full platform, showing deliberate scoping judgment and long-term system thinking — a story that plays well in interviews
