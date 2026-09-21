# 09 — Post-Hackathon Plan: Phase 3 (Coverage Gaps → Test Generation → Mutation-Grounded Evaluation)

**Status:** planning · **Started:** 2026-09-21 · **Author:** Harshit Anand

Vision Phase 3, the piece the evolution document calls "the hardest, most valuable part". Prior
phase: [08](08-post-hackathon-plan.md). Target architecture:
[`personal-project-delivery-intelligence-full.md`](../personal-project-delivery-intelligence-full.md).

The claim worth being careful about is not "AI writes tests" — plenty of tools do that. It is
**"only tests that demonstrably catch bugs are shown to a human"**, and that claim is only as good
as its evidence. So the evaluation layer is deterministic and the generation layer is the only
place an LLM is trusted to produce output.

---

## 1. Shape of the phase

| Stage | Who does it | Why |
|---|---|---|
| **Coverage gap** — which changed lines no test executes | coded tool | Parsing a coverage report and intersecting it with the diff is arithmetic. An LLM would only add a failure mode. |
| **Test generation** — write a test for a gap | LLM agent | The one genuinely generative step. |
| **Evaluation** — does the test catch real bugs? | coded tool | Mutation testing is an experiment with a measurable outcome: inject a bug, run the test, record whether it failed. Asking a model to grade its own output is what this phase exists to avoid. |

That is **one** new LLM agent, not three. The vision lists Coverage Gap and Test Quality Evaluation
as agents; reading what they actually do, neither needs a model, and Phase 2 established what an
unnecessary agent costs on a chain ([08 §6](08-post-hackathon-plan.md)).

## 2. Generation runs OUTSIDE the promotion chain

The decision chain must always reach a verdict — that is the product. Test generation is
advisory, slow (a model writing code, then a mutation campaign), and by design never blocks a
promotion. Putting it inline would risk the thing that matters for the thing that doesn't.

So:

- The promotion chain gains **no new step**. `finalize_run` already runs after `test_runner`, so it
  computes the coverage gaps there — deterministic, cheap, no extra turn.
- Generation + evaluation run **on demand**, in their own small network, invoked from the dashboard
  or the Gateway after a run finishes. Results attach to the run; nothing gates on them.
- **Nothing is ever auto-committed.** The output is a proposed diff a human accepts or discards.

## 3. Executing model-written code — the risk this phase introduces

Every other part of Sentinel *reads* code. This phase **runs code a model wrote**, which is a
different threat model and deserves saying plainly rather than burying in a tool docstring.

- Generated tests execute **only** inside the run's temporary workspace clone — never the source
  repo, never the Sentinel checkout.
- Execution goes through the existing `test_runner` subprocess path: a hard timeout, no shell, and
  the workspace is deleted afterwards.
- A generated test that never reaches a human still **executed** on this machine. The sandbox is a
  temp directory and a timeout — not a container, not a seccomp profile. That is an accepted limit
  for a local dev tool and an explicit blocker for running this against untrusted repositories;
  containerising the runner is the upgrade path, tracked with the K8s work in
  [07 §11](07-implementation-plan.md).
- Mutation runs execute the *project's own* code with small AST edits, which is the same trust
  level as running its test suite — no new exposure there.

## 4. Work items

| # | Item | Detail | Test |
|---|---|---|---|
| **P3.1** | `lib/coverage_parse.py` | Parse coverage.py JSON, Cobertura XML and lcov (JS) into `{file: {line: hits}}`. Formats chosen because they are what `coverage json`, `pytest --cov` and Istanbul already emit — no new tool asked of the target project | each format parses to the same shape on a fixture; a malformed file yields nothing, never an exception |
| **P3.2** | Coverage collection | `test_runner` adds `--cov` **only when coverage.py is importable in the target repo's environment**, writing JSON to temp. Failure to collect is not a test failure — coverage is a bonus, the test result is the product | a repo without coverage.py still runs tests and reports no coverage |
| **P3.3** | `coverage_gap` coded tool | Intersect `change_profile.functions_changed` with the coverage map → per-function `covered` / `partial` / `uncovered` + the specific uncovered line ranges. Prioritise: a gap inside a `sensitive_flags` file outranks an untested utility. Called from `finalize_run`, so the chain does not grow | a changed function with no executed lines ranks above a covered one; a sensitive-file gap outranks both |
| **P3.4** | `coverage_gaps` contract | Gap list + totals + the priority ordering, persisted like every other contract | validator round-trip |
| **P3.5** | `lib/mutate.py` | Deterministic AST mutation operators on one target function: flip comparison operators, off-by-one on integer literals, negate boolean returns, swap `and`/`or`, drop a statement. Each mutant is a source string plus a description | every operator produces a syntactically valid mutant that differs from the original; operators are stable across runs |
| **P3.6** | `test_evaluator` coded tool | For a proposed test: (1) it must **pass** against unmodified code — a test that fails on correct code is noise; (2) run it against each mutant and record which are **caught**; (3) mutation score = caught / total; (4) coverage contribution from re-running P3.1. Output is evidence, not an opinion | a real test catches the mutants; a tautological test (`assert True`) scores 0 and is rejected |
| **P3.7** | `test_generation_agent` + `registries/sentinel_testgen.hocon` | A small separate network: read the gap, the function source and two existing tests from the project (for style), emit a test file as a proposed diff. Never writes to the repo | live run produces a syntactically valid test for a planted gap |
| **P3.8** | `test_proposals` contract + table | Proposed diff, target function, mutation score, caught/missed mutants, coverage delta, status (`proposed`/`accepted`/`rejected`). Alembic revision | persisted and read back |
| **P3.9** | Gateway + SPA | `POST /api/v1/runs/{id}/generate-tests`, proposals on the run detail page with their mutation evidence, accept/reject (accept = download the diff, still no auto-commit) | a proposal renders with its score and both buttons work |
| **P3.10** | `scripts/verify_p3.py` | Fixture repo with a deliberately untested branch; asserts a gap is found, a test is generated, it passes on correct code, and it catches ≥ the threshold share of mutants | 3/3 |

## 5. What "good" means here

A generated test is surfaced only if **all** of these hold:

| Gate | Threshold | Why this and not a model's opinion |
|---|---|---|
| Passes on unmodified code | required | A test that fails on correct code is worse than no test |
| Mutation score | ≥ 0.5 of mutants caught | The single objective signal: it demonstrably detects broken behaviour |
| Adds coverage | ≥ 1 previously-uncovered line | Otherwise it is redundant with the existing suite |
| Not a tautology | no assertion on a literal | Cheap structural check for the classic LLM failure mode |

The thresholds are config, not code, and the first numbers are guesses to be replaced by measured
ones — the whole point of P3.10 is producing that measurement.

## 6. Deliberately out of scope

- **LLM critique of generated tests** ("does this mirror the implementation?"). The vision includes
  it; the mutation score is the harder, objective signal and it comes first. Adding a model opinion
  on top of an experiment is only worth it once the experiment is trustworthy.
- **JS/TS generation.** Python first: `ast` gives reliable mutation operators in the standard
  library. JS mutation needs tree-sitter surgery and a Stryker-shaped runner — a phase of its own.
- **Learning from outcomes** (did an accepted test later catch a real bug?). That is Phase 4, and
  it needs this phase's records to exist first.

## 7. Exit criteria

- [ ] `coverage_gap` finds the planted untested branch and ranks a sensitive-file gap above it.
- [ ] A generated test for that gap passes on correct code and catches ≥ 50% of mutants.
- [ ] A deliberately tautological test scores 0 and is rejected — the negative case is the one that
      proves the gate does anything.
- [ ] Nothing is written to the source repo at any point; proposals live in the database.
- [ ] `verify_b4` and `verify_p2` still pass — the promotion chain is untouched.
- [ ] Decision rate still 3/3.
