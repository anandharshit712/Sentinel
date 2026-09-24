# 10 — Post-Hackathon Plan: Phase 4 (Trust & Learning Loop)

**Status:** planning · **Started:** 2026-09-23 · **Author:** Harshit Anand

Vision Phase 4. Prior phases: [08](08-post-hackathon-plan.md) (review cluster),
[09](09-test-generation-plan.md) (test generation). Target architecture:
[`personal-project-delivery-intelligence-full.md`](../personal-project-delivery-intelligence-full.md).

Until now every run has been judged on evidence available *at the time of the run*. This phase adds
the only evidence that actually settles whether a decision was right: **what happened afterwards.**

---

## 1. The decision this phase turns on

The vision says outcomes should "refine risk thresholds" and "refine the Test Generation Agent's
prompting strategy". Read literally, that is a system that rewrites its own gate.

**This phase will not do that.** The loop produces *evidence and recommendations*; changing policy
stays a human action.

Why, concretely:

- The trust ladder is a **security and release gate**. A gate that silently re-tunes itself has no
  stable meaning — "risk 70 escalates" would be true this week and false next week, and nobody
  could tell from the config which it was.
- Auto-tuning on outcome data is exactly how a gate learns to be permissive: promotions that go
  badly are rare, so the arithmetic drifts towards "promote more" long before it has the evidence
  to justify it. Rare-event feedback loops fail silently and in the dangerous direction.
- It contradicts the project's spine (rule 4, [01 §D2](01-proposed-solution.md)): *LLM reasons,
  code decides* — and code decides from rules a human wrote and can read.

So: the system measures its own calibration honestly, says where it looks wrong, and proposes a
specific config change. A person applies it. That is a learning loop with a human in it, which is
the only kind worth putting in front of a release gate.

## 2. What counts as an outcome

| Outcome | Meaning | How it arrives |
|---|---|---|
| `reverted` | the promoted change was rolled back | recorded via the API (a CI hook can post it) |
| `incident` | an incident followed in that environment | recorded via the API; also already feeds `incident_history` |
| `clean` | a promotion that stood, after a settling window | recorded explicitly, or inferred by age |
| `escalation_upheld` | a human agreed with an escalate/hold | from the approvals table, already recorded |
| `escalation_overridden` | a human approved despite the gate | from the approvals table, already recorded |
| `generated_test_caught_bug` | an adopted test later failed on a real regression | recorded against the proposal |

The last four already have their data. Only `reverted`, `incident` and `clean` need a way in.

**The approvals table is the most valuable signal already sitting in this database.** Every time a
human approved an escalation, the gate was arguably too strict; every time they rejected one, it
was right. That is free calibration data the system has been collecting since the hackathon and has
never read.

## 3. Work items

| # | Item | Detail | Test |
|---|---|---|---|
| **P4.1** | `lib/calibration.py` | Deterministic statistics over decisions + outcomes + approvals: per band and per transition — how many promoted, how many went bad, how many escalations a human then approved. Confidence: a rate over 3 runs is not a rate, so every figure carries its sample size and is marked `insufficient_data` below a floor | rates computed by hand on a fixture match; a 2-sample cell reports insufficient rather than 50% |
| **P4.2** | Outcome recording | `POST /api/v1/runs/{id}/outcome` (approver) writing the existing `outcomes` table; `POST /api/v1/proposals/{id}/outcome` for adopted tests | round-trip, and an outcome for an unknown run is rejected |
| **P4.3** | `calibration_report` | Gateway endpoint + `scripts/calibration.py` for the same figures on the command line | report shape stable on an empty database (a new install must not crash) |
| **P4.4** | Recommendations | Deterministic rules over the statistics: "band `high` escalated 9 times, 8 were approved unchanged → the threshold may be too low, consider X". Each carries its evidence and sample size. **Emitted as advice, never applied** | a fixture with a clear pattern produces the matching recommendation; a fixture with thin data produces none |
| **P4.5** | Dashboard | A Calibration screen: decision counts by band, outcome rates, the recommendations with their evidence, and an explicit "nothing here changes the gate" note | renders on an empty database and on a seeded one |
| **P4.6** | Seed + `verify_p4.py` | Seeded history with a known pattern; asserts the statistics and the recommendation that should follow | 3/3 |

## 4. What this phase does NOT do

- **No automatic threshold changes.** §1.
- **No prompt auto-tuning.** Same reason, plus: with the provider's current variance
  ([09 §8](09-test-generation-plan.md)) any measured "improvement" from a prompt change would be
  indistinguishable from NIM having a good hour.
- **No ML model.** The data is tens to hundreds of runs. Rates with sample sizes are honest at that
  volume; a learned model would be a confident-looking way of overfitting a demo.

## 5. Exit criteria — met 2026-09-24

- [x] Outcomes can be recorded for a run and for an adopted test, and are read back
      (`POST /api/v1/runs/{id}/outcome`, `dao.record_outcome` / `list_outcomes`).
- [x] Calibration statistics computed from real rows, every figure carrying its sample size.
- [x] A seeded pattern produces the matching recommendation; thin data produces none.
      `verify_p4.py`: **12/12 checks pass** — 3 of 10 promotions at `medium` bad → 30% → flagged
      too loose; 9 of 10 escalations at `high` approved → 90% → flagged too strict; 3 observations
      → no rate and no recommendation.
- [x] The Calibration screen renders on an **empty** database (it explains what to do instead of
      showing empty tables) and on a seeded one. Verified in a browser.
- [x] Nothing in the loop writes to `config/` or changes a threshold — asserted by a test that
      greps the module for I/O, and carried in the payload itself as `applies_changes: false`.
- [x] `verify_b4`, `verify_p2` still pass; the promotion chain is untouched by this phase.

### What the first real report said

Run against this project's own history (77 decisions across 8 repos, real and fixture), the loop
produced three recommendations, all "possibly too strict" or "possibly too loose", all at moderate
confidence, none applied. Two observations worth keeping:

- **The evidence is mostly this project testing itself.** The report names the repos it read for
  exactly this reason: a gate recommendation derived from verification runs is a check that the
  machinery works, not advice about a real gate. Provenance is printed before any rate.
- **`unknown` outcome counts dominate.** 21 of 21 low-band promotions have no recorded outcome,
  because nothing has been recording them until now. The loop says so rather than treating silence
  as success — which is the single assumption that would make any gate look permanently correct.

### The limit that does not go away

The gate's false positives are unobservable. A change that was blocked never reveals what it would
have done, so no amount of history can prove a threshold is too strict — only that humans keep
overriding it. Every "possibly too strict" recommendation carries that sentence with it, so the
number cannot be quoted without the caveat.
