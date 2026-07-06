# 🚦 AI Delivery Intelligence Layer

### Multi-Agent Code Review + Smart Test Selection + Explainable Promotion Gating

### Built with Neuro-SAN | Cognizant Internal Hackathon

---

## 📌 Problem Statement

Every code change travels the same road: it gets **reviewed**, it gets **tested**, and it gets **promoted** through environments (Development → Testing → QA → Staging → Production). Today, each of these three checkpoints is broken in its own way — and worse, they don't talk to each other.

### Problem 1: Code Review Is a Slow, Inconsistent Bottleneck

A single PR often needs a senior developer for logic, a security engineer for vulnerabilities, and sometimes compliance input — people in different timezones with different bandwidth.

- Average PR review cycle in mid-to-large teams: **days, not hours**
- Review quality depends entirely on the individual reviewer's expertise and available time that day — one reviewer catches the SQL injection, another misses it
- Reviewers lose 20–30 minutes of deep-work productivity per context switch
- No single human reviewer holds full context across security, quality, and compliance simultaneously

### Problem 2: Test Suites Don't Scale With Codebases

Most CI pipelines run the **entire test suite on every change**, whether the change touched 3 lines in a logging utility or rewrote the payments core.

- Large suites take 30–90+ minutes per commit; engineers wait, context-switch, or stop trusting CI
- CI compute cost scales with codebase size and becomes a real infrastructure line item
- Flaky/slow CI breeds "just re-run it" culture, which defeats the purpose of testing

### Problem 3: Promotion Is a Black-Box, All-or-Nothing Decision

Environment promotion is governed by rigid binary logic: all tests pass → promote; anything fails → block. This ignores:

- That not all failures carry equal risk (a failure behind a feature flag ≠ a failure in the auth module)
- Context: Friday-evening production deploys, recent incident history, unusually large change batches
- The need for a reasoning trail — today, "why was this promoted?" is answered with "the rules said so"

### Problem 4 (The Meta-Problem): These Checkpoints Are Disconnected

Even where teams have review tools, test automation, and deployment gates, **the signals never flow between them.** A security concern raised in review doesn't influence which tests run. A risky change classification doesn't influence the promotion decision. Each gate re-derives context from scratch — or worse, ignores it.

---

## 💡 Solution Overview

**A single Neuro-SAN agent network that acts as a connected intelligence layer across the delivery lifecycle** — where the output of each stage becomes a risk signal for the next:

1. **Multi-Agent Code Review (First Pass)** — Specialized Security and Code Quality agents review every change in seconds, producing structured findings with severity levels. Human reviewers are freed to focus on architecture and business logic.

2. **Smart Test Selection** — The system reasons about what actually changed (diff + dependency graph) and runs only the relevant subset of the existing test suite, plus a safety-net smoke set.

3. **Explainable Promotion Gating** — Review findings, test results, change profile, and environment context all flow into one risk score, and a Promotion Gating Agent applies a **graduated trust ladder** to produce a decision (promote / hold / escalate) with a full, human-readable reasoning trail.

### The Differentiator: Cross-Stage Signal Flow

The "wow" of this system is not any single agent — it's that **a security finding in review directly raises the promotion risk score and can trigger human escalation**, automatically, with the reasoning visible. No commercial tool connects these stages today.

### Critical Design Principle: Augment, Don't Replace

This is **not** a new CI/CD engine and **not** a replacement for human reviewers. It plugs into existing pipelines (Jenkins, GitHub Actions, GitLab CI) as an intelligence stage, and acts as a first-pass expert filter before human review. Teams keep their tooling; they gain a decision layer.

### Critical Design Principle: Language & Framework Agnostic — Honestly

The system detects a project's existing tooling via manifest files (`package.json`, `pom.xml`, `requirements.txt`, `go.mod`, etc.) and invokes the **project's own test commands**, reasoning over the output rather than reimplementing any test framework.

---

## 🏗️ Approach & Architecture

### Framework: Neuro-SAN (HOCON-based Agent Network)

The entire network is declared in HOCON configuration — each agent independently configured with its own system prompt, LLM, and CodedTools, communicating via the AAOSA protocol.

---

### High-Level Agent Network Design

```
                    ┌───────────────────────────────┐
                    │   🔔 PR / Pipeline Trigger    │
                    │  (GitHub / Jenkins / GitLab)  │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼──────────────────┐
                    │      🎙️ Frontman Agent          │
                    │   (Delivery Coordinator)         │
                    └──┬──────────┬──────────────────┬─┘
                       │          │                  │
       ┌───────────────▼───┐ ┌────▼─────────────┐ ┌──▼──────────────────┐
       │ 🧩 Change        │ │ 🔒 Security      │ │ ✅ Code Quality     │
       │ Analysis Agent    │ │ Review Agent     │ │ Review Agent        │
       │ (diff + dep graph)│ │ (OWASP, secrets) │ │ (SOLID, complexity) │
       └────────┬──────────┘ └────────┬─────────┘ └────────────┬────────┘
                │                     │                        │
                │                     └───────────┬────────────┘
                │                                 │
                │                    ┌────────────▼────────────┐
                │                    │  📋 Review Synthesis    │
                │                    │  Agent (unified report) │
                │                    └────────────┬────────────┘
                │                                 │
   ┌────────────▼─────────────┐                   │
   │  🎯 Test Selection Agent │                  │
   │  (relevant subset only)  │                   │
   └────────────┬─────────────┘                   │
                │                                 │
   ┌────────────▼────────────┐      ┌────────────▼─────────────┐
   │  ⚙️ Test Execution     │       │  📜 Environment Context │
   │  Interface (CodedTool)  │      │  Agent (per-env risk)    │
   └────────────┬────────────┘      └────────────┬─────────────┘
                │                                 │
                └───────────────┬─────────────────┘
                                │
                   ┌────────────▼──────────────┐
                   │   📊 Risk Scoring Agent   │
                   │  (review findings + test  │
                   │   results + context)      │
                   └────────────┬──────────────┘
                                │
                   ┌────────────▼───────────────┐
                   │ 🚦 Promotion Gating Agent │
                   │  (trust ladder decision)   │
                   └────────────┬───────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
  ✅ Auto-Promote       ⏸️ Hold + Notify       🆘 Escalate to Human
  (low-risk envs)       (medium risk)          (high-risk / prod)
```

---

### Agent Breakdown

#### 1. 🎙️ Frontman Agent — _Delivery Coordinator_

- **Role:** Entry point for PR submissions and pipeline stage transitions. Extracts metadata (repo, branch, commit SHA, PR description, language, target environment) and delegates in parallel.
- **LLM:** GPT-4o / Claude Sonnet

#### 2. 🧩 Change Analysis Agent

- **Role:** Determines what structurally changed
- **LLM:** GPT-4o
- **CodedTools:** Git diff parser, AST analyzer (tree-sitter for multi-language support), dependency graph builder
- **Output:** Change profile — files/functions changed, blast radius, change classification (feature / bug fix / refactor / config), sensitive-area flags (auth, payments, data deletion, migrations, public APIs)

#### 3. 🔒 Security Review Agent

- **Role:** Deep-scan the diff for security vulnerabilities
- **LLM:** GPT-4o
- **Checks:** OWASP Top 10 patterns (SQL injection, XSS, CSRF), hardcoded secrets/API keys, insecure dependencies and known CVEs, input validation and authentication flaws, unsafe deserialization, path traversal, command injection
- **Output:** Findings with severity (Critical / High / Medium / Low) and fix suggestions — **also passed to the Risk Scoring Agent as a promotion signal**

#### 4. ✅ Code Quality Review Agent

- **Role:** Review for maintainability and engineering best practices
- **LLM:** Claude Haiku / GPT-4o-mini (cost-efficient pattern work)
- **Checks:** SOLID adherence, DRY violations, naming/readability, cyclomatic complexity, error-handling gaps, missing test coverage on new functions
- **Output:** Quality score + refactor suggestions with line references

#### 5. 📋 Review Synthesis Agent

- **Role:** Consolidates Security + Quality findings into one prioritized, de-duplicated review report for the developer: executive summary, severity-ranked issues, PR health score (0–100), and a recommendation (✅ Approve | ⚠️ Approve with Changes | ❌ Request Changes)
- **LLM:** GPT-4o / Claude Sonnet
- **Key point:** This report is delivered to the developer immediately (seconds, not days) **and** forwarded downstream as structured input to Risk Scoring

#### 6. 🎯 Test Selection Agent

- **Role:** Decides which existing tests are relevant for this specific change
- **LLM:** GPT-4o-mini
- **How it works:** Cross-references the dependency graph and blast radius against the project's test suite (mapped via static analysis / existing coverage maps). Selects (a) tests directly covering changed files, (b) tests covering downstream dependents, (c) a baseline smoke-test set that always runs as a safety net. For changes with sensitive-area flags, selection expands conservatively rather than narrowly.
- **Design note:** Selection is grounded in deterministic dependency-graph analysis, with the LLM reasoning over edge cases and explaining inclusions/exclusions — not free-form LLM guessing. This keeps the false-negative risk (skipping a test that would have caught a bug) low and auditable.
- **Output:** Minimal-but-sufficient test execution plan, with reasoning

#### 7. ⚙️ Test Execution Interface (CodedTool — not an LLM agent)

- **Role:** Deterministically executes selected tests using the project's own test runner, detected from manifest files
- **Output:** Structured pass/fail/skip results, stack traces, timing, coverage delta

#### 8. 📜 Environment Context Agent

- **Role:** Supplies contextual risk signals for the target environment
- **LLM:** Claude Haiku
- **Checks:** Recent incident history, timing risk (Friday-evening prod deploys), current environment stability, change batch size
- **Output:** Environment risk context + flags

#### 9. 📊 Risk Scoring Agent

- **Role:** The convergence point — synthesizes **review findings + test results + change profile + environment context** into one weighted risk score (0–100) with a structured explanation of contributing factors
- **LLM:** GPT-4o
- **Example:** A Critical security finding from the Security Review Agent alone can push the score into "escalate" territory even if all tests pass — this cross-stage influence is the core innovation of the merged system

#### 10. 🚦 Promotion Gating Agent — _Final Decision Maker_

- **Role:** Applies the graduated **trust ladder** and produces the decision with full reasoning
- **LLM:** GPT-4o / Claude Sonnet
- **Trust ladder:**
  - **Dev → Test:** Auto-promote unless risk is very high
  - **Test → QA:** Auto-promote on low risk; hold + notify on medium/high
  - **QA → Staging:** Recommend + human approval on anything above low risk
  - **Staging → Production:** **Always** a recommendation + mandatory human approval, regardless of score — the agent never auto-deploys to production
- **Output:** Promote / Hold / Escalate + a human-readable explanation: what the review found, what was tested and why, what passed/failed, what contextual factors were weighed, and why the decision was made

---

### Why the Trust Ladder Matters for Real Adoption

A system claiming fully autonomous production deployment is both a hard sell and a genuine risk. The graduated model — full automation only where mistakes are cheap, mandatory human-in-the-loop where they're expensive — makes the system realistic to pilot, easy to explain to engineering leadership, and naturally extensible: automation thresholds can be relaxed over time as real usage data builds trust.

---

### Technology Stack

| Component                | Technology                                                                   |
| ------------------------ | ---------------------------------------------------------------------------- |
| Agent Orchestration      | Neuro-SAN (HOCON config)                                                     |
| Agent Communication      | AAOSA Protocol                                                               |
| LLM Providers            | OpenAI GPT-4o / GPT-4o-mini, Claude Sonnet/Haiku                             |
| CI/CD Integration        | GitHub Actions / Jenkins / GitLab CI APIs (pluggable)                        |
| Code Input               | GitHub webhook / manual PR diff paste                                        |
| Change & Review Analysis | Git, tree-sitter (multi-language AST), dependency graph builder, CVE checker |
| Test Execution           | Native project test runners via CodedTool subprocess wrapper                 |
| Risk History Store       | Postgres/SQLite — past decisions and outcomes                                |
| UI                       | NSFlow + lightweight dashboard (review report + promotion reasoning trail)   |
| Secure Data Handling     | Neuro-SAN `sly_data` channel — code never leaks into unintended prompts      |

---

### Data Flow (End to End)

1. Developer opens a PR / pushes a commit; webhook triggers the Frontman Agent
2. Change Analysis, Security Review, and Code Quality Review agents run **in parallel**
3. Review Synthesis Agent produces the developer-facing review report (delivered in seconds)
4. Test Selection Agent uses the change profile to build a minimal relevant test plan
5. Test Execution Interface runs the selected tests via native tooling
6. Environment Context Agent gathers per-environment risk signals
7. Risk Scoring Agent combines **review findings + test results + change profile + environment context**
8. Promotion Gating Agent applies the trust ladder → decision + reasoning
9. Low-risk transitions execute automatically via CI/CD API; higher-stakes transitions post a recommendation to the release manager (dashboard/Slack) for approval
10. Outcomes (reverts, incidents) are logged to the Risk History Store to inform future scoring

---

## 🎯 Success Metrics

| Metric                          | Current State                         | Directional Goal                             |
| ------------------------------- | ------------------------------------- | -------------------------------------------- |
| First-pass review turnaround    | Days                                  | Seconds–minutes                              |
| CI test runtime per commit      | Full suite (30–90 min in large repos) | Relevant subset + smoke set                  |
| CI compute spend                | Baseline                              | Meaningful reduction via selective execution |
| Promotion decision transparency | Binary pass/fail, no reasoning        | Full explainable reasoning trail             |
| Human reviewer focus            | All issues, including basics          | Architecture, business logic, mentorship     |
| Review → deployment signal flow | None (disconnected gates)             | Review findings directly influence gating    |

_(Figures like "30–90 min" reflect commonly reported ranges for large monorepos; pilot measurements on real repos would establish the actual baseline.)_

---

## 🚀 Hackathon Scope (MVP)

1. ✅ Working Neuro-SAN network with all 10 components configured in HOCON
2. ✅ Sample multi-language repo (small Python service + small Node.js service) to prove language-agnostic detection
3. ✅ **Demo Run 1 — the happy path:** push a small, low-risk change → parallel review agents return a clean report → Test Selection picks a small relevant subset → tests pass → low risk score → auto-promote, with the full reasoning trail displayed
4. ✅ **Demo Run 2 — the escalation:** push a change touching the auth module with a planted vulnerability (e.g., string-concatenated SQL) → Security Review Agent flags it Critical → risk score spikes **even though all tests pass** → Promotion Gating escalates to human approval, with the reasoning explicitly citing the security finding
5. ✅ Side-by-side dashboard/NSFlow view of both reasoning trails

Demo Run 2 is the money shot: it demonstrates in one screen that connected signals catch what binary test-pass/fail gating structurally cannot.

**Stretch Goals:**

- Real GitHub Actions integration (live webhook, not simulated)
- Risk History Store influencing a repeat decision after a logged incident
- Slack/Teams notification for the human-approval step

---

## 📦 Why Cognizant Should Adopt This

- **Reduces CI/CD infrastructure spend** via selective test execution — a quantifiable, CFO-friendly metric
- **Compresses review turnaround from days to seconds** for the first pass, freeing senior engineers for high-value review
- **Every promotion decision has a logged, explainable reasoning trail** — valuable for post-incident reviews and audit compliance
- **Safe-by-design adoption path** — the trust ladder allows piloting on low-risk environments first
- **Plugs into existing tooling** — no migration off Jenkins/GitHub Actions/Azure DevOps
- **Reusable across every engineering team**, regardless of language or framework

---

## 🔭 Beyond the Hackathon

The natural next capability — deliberately out of MVP scope — is **AI-assisted test generation with objective quality evaluation**: identifying coverage gaps in changed code, generating idiomatic tests for them, and validating those tests via mutation testing before a human ever sees them. This extends the same network with three additional agents and turns the platform from "smarter gates" into a system that actively improves the codebase's safety net over time.
