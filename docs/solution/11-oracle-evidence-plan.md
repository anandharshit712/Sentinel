# 11 — Oracle Evidence: fixing the regression-lock gap in test generation

**Status:** O1–O4 + O6 built · **Started:** 2026-09-24 · **Updated:** 2026-09-25 · **Author:** Harshit Anand

Follows [09](09-test-generation-plan.md) (test generation) and [10](10-learning-loop-plan.md).
This document exists because Phase 3 shipped with a hole in its central claim, and the hole is
structural rather than a bug to patch.

---

## 1. The gap, demonstrated

`test_evaluator` was run on a function whose docstring promises 15% off and whose code gives 10%:

```python
def loyalty_discount(total, years):
    """Members of 3+ years get 15% off. Everyone else pays full price."""
    if years >= 3:
        return round(total * 0.90, 2)   # bug: 10%, not 15%
    return total
```

A generated test asserting `loyalty_discount(100.0, 3) == 90.0` — the **buggy** output — was graded:

```
verdict        : accepted
mutation score : 0.833  (5 of 6 injected bugs caught)
```

Everything behaved as designed. The result is a test that permanently enforces the bug: the day
someone fixes the discount to 15%, this test goes red and reports the *fix* as the breakage.

## 2. Why mutation testing cannot fix this, ever

Mutation testing asks **"if I break the code, does the test notice?"** — sensitivity. It says
nothing about whether what the test expects is *right*. When test and implementation are wrong
together, they agree, and agreement is all mutation testing can see.

This is not a Sentinel defect. A 2026 study over 24 open-source repositories found LLM-generated
oracles **capture actual program behaviour rather than expected behaviour**, the same limitation
EvoSuite and Randoop have ([arXiv 2410.21136](https://arxiv.org/abs/2410.21136)). The literature's
terms are worth adopting:

| | asserts | can catch a bug? |
|---|---|---|
| **regression oracle** | what the code does | no — it encodes the bug |
| **specification oracle** | what the code should do | yes |

Phase 3 built a very good regression-oracle generator and presented it as a correctness gate.

**The first thing to fix is therefore not a model — it is the claim.**

## 3. The design principle

> The implementation must never be the only witness to its own correctness.

Every oracle tier below is an *independent* witness. The system reports which witnesses agreed, and
never collapses them into one number that implies more than it knows.

| Tier | Oracle | Witness | Deterministic? |
|---|---|---|---|
| **0** | Honest labelling — no independent witness means *characterization test*, said plainly | — | ✅ |
| **1** | **Differential**: run the test against `base_sha` and `head_sha` | the other commit | ✅ |
| **2** | **Blind oracle**: derive the expectation from signature + docstring + PR text, *never seeing the body* | stated intent | ✗ (one LLM call) |
| **3** | **Properties / metamorphic**: bounds, idempotence, type preservation | mathematics | ✅ |
| **4** | Mutation score (built in Phase 3) | the implementation | ✅ |

Tier 4 is the only non-independent tier. That is precisely why it certified a bug.

## 4. Tier 1 is nearly free here

Differential testing finds 21–34% more behaviour changes than regression testing alone, and
[Testora](https://arxiv.org/abs/2503.18597) (ICSE 2026) pairs it with PR text to separate intended
changes from regressions — 19 real regression bugs found, 11 of 13 confirmed by maintainers, at
12.3 min and $0.003 per PR.

Sentinel already holds every input: both SHAs, a full clone (not shallow — the Gateway clones full
history precisely so both are reachable), the PR title and description on the `DeliveryEvent`, and
a working test runner. Tier 1 needs no new data and no model.

What it answers is more useful than "is this test correct":

- **passes on base AND head** → the test locks *pre-existing* behaviour. It cannot encode a bug
  this change introduced. Safe as a regression guard, labelled as one.
- **passes on head, fails on base** → the test asserts behaviour this change *altered*. That is the
  reviewer's question, put precisely: `before: 100.0 · after: 90.0 · the PR says "add loyalty
  discount" — intended?`
- **target absent on base** → new code; there is no previous behaviour to compare.

## 5. The reframing that makes this a feature rather than a caveat

> **A generated test that disagrees with intent is worth more than one that agrees.**

Today a disagreement is invisible: the system silently sides with the code. Under this design a
Tier 2 or Tier 3 disagreement is **a finding about the code**, emitted into the review report
beside the SQL injection and the N+1 query as `spec_implementation_mismatch`.

That is how Testora found real bugs, and it fits what Sentinel already is — a system whose product
is findings, not artifacts.

## 6. What a proposal says afterwards

`accepted · 1.00` is replaced by a classification naming its evidence:

| Classification | Meaning | Adopt? |
|---|---|---|
| `regression_guard` | base ≡ head, mutation-sensitive | yes — labelled "locks existing behaviour" |
| `change_documented` | behaviour changed and intent agrees | yes — the strongest kind |
| **`disputed`** | intent and implementation disagree | **no — raise a finding on the code instead** |
| `characterization` | no docstring, no PR text, no property to check | yes, labelled unverified |

## 7. Work items

| # | Item | Detail | Test |
|---|---|---|---|
| **O1** ✅ | `lib/differential.py` | Run a test against a `git worktree` of `base_sha` and against head; classify unchanged / changed / new. No model, no network | a test over unchanged code passes both; over changed code passes head and fails base; a missing target reports `new_code` |
| **O2** ✅ | Tier 0 labelling | `test_proposal` gains `classification` + `oracle_evidence`; evaluator stops returning a bare `accepted` | a proposal with only a mutation score classifies as `characterization`, never as verified |
| **O3** ✅ | Evaluator integration | `test_evaluator` calls O1 when a base SHA is available and merges the evidence | evidence present on a live run; absent base degrades gracefully |
| **O4** ✅ | `lib/spec_check.py` (Tier 3) | Built as a *documented-rate* check rather than general invariants: a docstring stating 15% implies a multiplier of 0.85, so a function scaling by 0.90 contradicts its own documentation. Static, no execution. Broader invariants (bounds, idempotence) deferred — this one covers the demonstrated failure and has a far lower false-positive surface | the §1 case is caught; correct code, a fee, a direct rate and an undocumented function are all silent |
| **O5** | Blind oracle agent (Tier 2) | ⬜ **not built.** One agent in `sentinel_testgen`, given signature + docstring + PR text and **never the body**; returns its own expected values. `lib/intent.py` already assembles and redacts its input, so what remains is the agent itself | given a mismatching docstring it disputes; given agreement it confirms |
| **O6** ✅ | `spec_implementation_mismatch` finding | Disputes flow into `review_report` through the existing floor mechanism | a disputed proposal produces a finding at `medium`+ |
| **O7** ⚠️ | Dashboard done; `verify_o.py` not written | Evidence shown per tier; the demo fixture in §1 must end as `disputed`, not `accepted` | 3/3, and the §1 case is caught |

## 8. Build order and why

O1 → O2 → O3 first: all deterministic, all survive a provider outage, and they remove the overclaim
immediately. O4 next (still deterministic). O5 is the only new LLM dependency and lands last, when
the evidence it joins is already trustworthy.

## 9. The limit that remains

The oracle problem is undecidable in general: nothing can know what code *should* do without being
told. A blind oracle is silent on undocumented functions; Tier 1 says nothing about bugs older than
the diff; [AugmenTest](https://arxiv.org/pdf/2501.17461), the state of the art in doc-derived
oracles, reports ~30% success at its strictest threshold.

What this design fixes permanently is narrower and real: **the system stops presenting "the test
agrees with the code" as "the test is correct", and surfaces every disagreement it can detect
instead of silently resolving it in the code's favour.** That is a property of the design rather
than a capability of a model, which is why it holds.


---

## 10. Built so far — 2026-09-25

| | |
|---|---|
| **Tier 1 differential** | `lib/differential.py`; base worktree, never mutates the run workspace; unchanged / changed / new_code / unknown |
| **Tier 3 specification** | `lib/spec_check.py`; static rate check, no model, no execution |
| **Tier 2 input** | `lib/intent.py`; assembles signature, docstring, types, PR text, call sites — and **redacts the body**, with a test that fails if redaction ever leaks the implementation |
| **Classification** | replaces the bare verdict everywhere: `regression_guard`, `change_documented`, `characterization`, `disputed`, `rejected` |
| **Finding** | a contradiction is reported on *every* run through `report_publisher`'s deterministic floor, not only when a test was requested |
| **Dashboard** | the classification leads, each tier states its witness, and a `disputed` proposal offers no Adopt button |

**The demonstrated case, before and after:**

```
before   accepted · mutation score 0.83 · adoptable as a verified test
after    disputed · "the docstring states 15%, which implies a multiplier of 0.85,
                     but loyalty_discount scales by 0.9" · not adoptable
                   · reported as a medium finding on the run
```

**Still open:** O5, the blind oracle — the general case, for functions whose documentation states
intent in prose rather than a number. Its input pipeline exists; only the agent is missing. Until
then Tier 3 covers the numeric-claim subset deterministically, which is the commonest form and the
only one that can be checked without a model.
