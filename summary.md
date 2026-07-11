# Sentinel — Project Summary

**Author:** Harshit Anand

## The problem

Software delivery today runs through three gates that don't talk to each other. Code review is a slow, inconsistent human bottleneck — hours to days per change, quality dependent on who happens to be available. CI reruns the entire test suite on every change, so cost and latency scale with repo size, not with how much actually changed. Promotion is binary pass/fail: no risk weighting, no context, no reasoning trail for why a change was allowed through. Worst of all, these three gates are disconnected — a critical security finding surfaced in review has no bearing on the promotion decision as long as an unrelated test suite is green. A hardcoded credential or a SQL injection can ship simply because nothing wires review output into promotion policy.

## The solution

Sentinel is an AI delivery intelligence layer that sits beside existing CI/CD (GitHub Actions — it augments, it doesn't replace) and connects review, test selection, and promotion into a single reasoning chain:

1. **Multi-agent first-pass review** — specialist agents produce a severity-ranked, deduplicated review report in seconds, with a security-review fan-out that adaptively scales from one to four reviewer shards based on how much changed, plus a senior-agent executive summary.
2. **Smart test selection** — a deterministic diff + dependency-graph + test-mapping pipeline selects the relevant test subset plus an always-on smoke set, then runs the project's *own* real test runner (`pytest`, `jest`) — not a mock, not a simulation.
3. **Explainable promotion gating** — review findings, test results, change profile, and environment context converge into one deterministic, versioned risk score. A graduated trust ladder turns that score into **promote / hold / escalate**, with a full reasoning trail citing the exact findings and factors behind the number. Staging → production is hard-floored to always require human approval, regardless of score.

The governing design principle is **"LLM reasons, code decides."** Every scoring formula, policy threshold, and test execution step is a deterministic coded tool. LLM agents interpret findings, write explanations, and may only ever **raise** a risk score — never lower one. This is what makes the system's output auditable rather than a black box: every decision traces back to a versioned formula and a logged rule, not a prompt.

## How it's built

The entire pipeline is one **Neuro-SAN** multi-agent network — Neuro-SAN is used as the sole orchestrator, per the hackathon's core requirement, not as a library called from otherwise-conventional code. A FastAPI Delivery Gateway fronts the network: it accepts delivery events (webhook, manual, or CI-triggered), clones the target repo, streams live agent progress to a React dashboard over SSE, and persists every run's findings, scores, and decisions to PostgreSQL. The dashboard shows the agent network firing node-by-node in real time, alongside review, test, and risk cards, an approval queue (rejections require a written reason — no rubber-stamping), an audit log, and a run-comparison view.

A single GitHub Actions workflow drops into any repository and posts every PR to the same Gateway endpoint the manual demo uses — no separate integration path for real usage versus demo usage.

## Where it stands

The full nine-step pipeline runs headless end to end against two rehearsed scenarios: a benign change that scores near-zero risk and auto-promotes, and a change carrying a planted hardcoded AWS key and a SQL injection that scores at the critical band and is escalated for human approval — despite every test passing. Both are reproducible through the live Gateway with the dashboard streaming in real time. An audit mode extends the same pipeline to scan an entire existing repository in one pass rather than only new changes, reporting exactly how much of the codebase was covered.

JavaScript/TypeScript repos are supported alongside Python through the same static-analysis and test-selection path; Java, Go, and Rust are deliberately out of scope for this build (each needs its own grammar and import-resolution work, not just configuration) but the architecture is built to add them without touching the orchestration layer.

## Why it matters

Sentinel doesn't add another isolated check to a pipeline that already has too many. It makes the checks that already exist talk to each other, so a finding that matters can't be silently overridden by an unrelated green test suite — and every decision it makes comes with a reason a human can read, question, and act on.
