# Real-repo trials: what Sentinel found outside its fixtures

**Updated:** 2026-10-09 · Post-hackathon Phase 5

Fixtures are written to be caught. This document records what happened when Sentinel was pointed at
code nobody wrote for it. There are two kinds of trial:

1. **The review pipeline** (`scripts/run_repo.py --full`, audit mode) on whole repositories. The
   question: what do the deterministic floor and the LLM reviewers each contribute?
2. **The standalone test evaluator** (`python -m lib.evaluate`) on a mature open-source library's
   own tests. The question: does mutation scoring say anything a maintainer would act on?

Every number here comes from a run recorded in the database or a JSON result file. None of it is
estimated.

---

## 1. Review pipeline on real repositories

Audit mode diffs a repository against the git empty tree, so every file reads as added. The
promotion decision is meaningless in that mode (risk is churn-driven), so read the findings.

| repo | language | added lines | runs |
|---|---|---|---|
| `OWASP/NodeGoat` | JS | 6,861 | 07-11 → 10-09 |
| `anandharshit712/ORION` | Python + JS monorepo | 29,806 → 63,229 (repo grew) | 07-11 → 10-09 |
| `anandharshit712/Accident_Detection_System` | Python + JS | 35,628 | 07-11 |
| `sanjanamohanty25/Multiagent_migration_system` | Python / PowerShell | (last-commit mode) | 07-10 |

### 1.1 The deterministic floor carries the security product

On every repository, every security finding (secrets, dangerous sinks, dependency advisories) came
from deterministic rules (`source: tool`). Not once did an LLM security reviewer add a security
finding the rules had missed.

That's partly by design. Each reviewer deep-reads a bounded slice (`review_budget_lines`, 40–800
lines) out of thousands, while the rules scan 100%. Mostly, though, it's because for months the LLM
layer wasn't working at all, as §1.3 shows.

### 1.2 Precision of the floor improved by an order of magnitude

| NodeGoat audit | criticals | of which `high_entropy_secret` |
|---|---|---|
| 2026-07-11 | **126** | 120 |
| 2026-10-09 | **6** | 0 |

The July criticals were mostly config and generated files tripping the entropy check, such as
dotted Android permission names (`android.permission.ACCESS_FINE_LOCATION` scores 4.37 bits). The
August fix (token shape, an entropy floor, rejecting dotted identifiers, dropping files marked as
generated) took the noise out without removing a real credential. The six remaining criticals are
hardcoded credentials in NodeGoat's config files, which are genuine.

### 1.3 Why the LLM layer contributed nothing for months: four defects, none of them the model

Each one hid behind the deterministic floor. The report always had findings, so the runs looked
fine.

| # | defect | how it showed up | fixed |
|---|---|---|---|
| 1 | `contract_store` rejected payloads that models send as JSON *strings* | 89 calls, 1 write per run, the agent retrying until it gave up | 2026-09-21 |
| 2 | Reviewer instructions required copying every tool finding into the contract **verbatim** | the model had to regenerate up to 50 findings as output tokens, which ran past the socket timeout before the write | 2026-10-09 |
| 3 | `dependency_graph` returned the entire change profile (every hunk) to the LLM | ORION: **~830k tokens**, `400: 1,139,081 tokens > 1,000,000 max` | 2026-10-09 |
| 4 | Reviewer snippets had a line budget but no size budget | ORION: 800 snippets with context = **~140k tokens**, then a timeout | 2026-10-09 |

Defects 2–4 share one root cause: **an LLM-facing tool result sized by the repository rather than
by the model.** The fix follows the same rule as the rest of the system (*LLM reasons, code
decides*). The model gets a bounded view and code keeps the complete data:

| tool (ORION audit) | before | after |
|---|---|---|
| `dependency_graph` | ~830k tokens | ~1k |
| `secret_scanner` shard | ~140k tokens | ~36k |
| `complexity_metrics` | unbounded | top 40 functions, exact totals |

Nothing reaches the report through an LLM any more. The floor (secrets, sinks, dependency
advisories, performance and compliance rules, spec mismatches) is recomputed in code by
`report_publisher`, so capping the LLM's view can't lose a finding. Dependency advisories were the
one exception until 10-09: they reached the report only if a reviewer transcribed them. They're now
part of the floor.

### 1.4 What the LLM reviewers add, now that they can

**ORION, 2026-10-09, before and after fixes 2–4** (63,229 added lines):

| | before | after |
|---|---|---|
| wall clock | 799 s | **355 s** |
| reviewer contracts written | 2 of 5 (security, performance and compliance timed out) | **5 of 5**, zero rejections |
| context overflow (`400`) | yes, in change analysis | none |
| LLM-sourced findings | 11 (all complexity) | 17 |

All 17 after-fix LLM findings were checked by hand against the source:

| LLM finding | verdict |
|---|---|
| `pickle.loads` in a model sandbox | **The floor already had it.** It was credited to the LLM because `_dedup` kept whichever finding arrived first on a tie. Fixed: on equal severity the rule's finding wins, so the LLM layer's contribution is no longer overstated |
| "Hardcoded secret" ×2, *critical*, in a frontend test | **False positive.** `orion_sk_live_0123456789abcdef…` is an obviously fake fixture |
| "XSS" ×2 on `/^([A-Z]{3})-\d+/.exec(...)` | **False positive.** That's `RegExp.exec`. The floor got it wrong too, reporting it as `code_injection`; the rule now skips regex-literal `.exec(` and still flags `child_process.exec(` |
| Personal data in a committed `*.local.json` editor-settings file ×2 | **Real, and no rule covers it.** Local user paths leak through a file that shouldn't be committed. Minor, so "high" overstates it |
| Complexity ×10 at line 1 | restated `complexity_metrics` output |

So, of 17: 2 real findings no rule could make, 1 duplicate of the floor, 4 false positives (two at
critical), and 10 restatements.

**NodeGoat, 2026-10-09** (6,861 lines, after defect 1 was fixed, with defects 2–4 still present):
279 s (the August run took 592 s), no timeouts, 38 findings, of which **10 came from LLMs**. All 10
were code-quality findings (complexity, function length, error handling) on three DAO files and one
route, and all were pinned at line 1. The security reviewer's contract was written after one
rejected attempt (a Python-repr payload, now accepted by `contract_store`), and it added nothing
beyond the floor across its 40-line slice.

**Across all trials, LLM findings fall into two groups:**

- **Restated measurements.** "High cyclomatic complexity (25) in `_apply_yaml`" is
  `complexity_metrics` output turned into prose. These are useful as wording but carry no new
  information, and they're reported at line 1 because the model doesn't localise them.
- **Genuinely new, and rare.** On Accident_Detection_System the reviewer flagged *hardcoded absolute
  Windows dataset paths* in two training scripts (`frame_extraction.py:53`, `training.py:13`). No
  rule covers that, it's a real portability defect, and it's correctly localised.

Two floor defects were found the same way, and both are fixed: the `RegExp.exec` false positive
above, and the tie-break that credited a rule's finding to the LLM.

**Verdict.** The deterministic floor is the product. The LLM layer adds explanation and occasional
judgement on a bounded slice. That's consistent with the design rule that the LLM may only raise a
concern, never decide. A gate whose security depended on the model would have been silently empty
for the three months described in §1.3.

---

## 2. The test evaluator on a mature library

**Subject:** [`python-humanize/humanize`](https://github.com/python-humanize/humanize) at
`785e5dc` (2026-10-06). It's widely used, heavily tested, and pure Python. Each function was
evaluated against **its maintainers' own test file**, with 20 mutants per function:

```bash
python -m lib.evaluate --repo humanize --target src/humanize/number.py \
    --function clamp --test humanize/tests/test_number.py --max-mutants 20 --json
```

| function | test file | score | caught | surviving mutants |
|---|---|---|---|---|
| `intword` | test_number.py | **1.00** | 17/17 | — |
| `apnumber` | test_number.py | **1.00** | 6/6 | — |
| `scientific` | test_number.py | **1.00** | 6/6 | — |
| `naturalsize` | test_filesize.py | 0.95 | 19/20 | `exp < len(suffix)` → `<=` |
| `ordinal` | test_number.py | 0.90 | 9/10 | `gender == "male"` → `!=` |
| `fractional` | test_number.py | 0.90 | 9/10 | `limit_denominator(1000)` → `1001` |
| `clamp` | test_number.py | 0.80 | 8/10 | `value < floor` → `<=`, `value > ceil` → `>=` |
| `metric` | test_number.py | 0.75 | 15/20 | exponent thresholds `33`, `-30`, `30` ± 1 |
| `intcomma` | test_number.py | **0.44** | 4/9 | the locale-translation branch |

Median 0.90 across nine functions. Each campaign took 30–110 s on a Windows laptop, because every
mutant is a fresh pytest process.

### 2.1 Reading the survivors

A surviving mutant is a question, not a verdict. Classified by hand:

| survivor | what it is |
|---|---|
| **`clamp`: `<` → `<=`, `>` → `>=`** | **A real gap, confirmed by execution.** At exactly the floor the original returns `'0.01'` and the mutant returns `'<0.01'`; the same happens at the ceiling. `test_clamp` has eleven cases and none puts the value on the boundary. Missing test: `([0.01, "{:.0%}", 0.01], "1%")` |
| `metric`: thresholds ± 1 | A real but extreme-range gap: no test at exactly 10³³, 10⁻³⁰ or the 10³⁰ rounding carry |
| `naturalsize`: `exp < len(suffix)` | A real but extreme-range gap: rounding that carries past the *largest* suffix is untested |
| `fractional`: `1000` → `1001` | Close to an equivalent mutant: it only changes the approximation of fractions with denominators near 1000 |
| `ordinal`: `gender == "male"` → `!=` | **An equivalent mutant.** English ordinal suffixes are the same for both genders, so no English-locale test can kill it. That's a limitation of the method, not of the tests |
| `intcomma`: the locale branch | **A scoping effect.** That branch is exercised by `test_i18n.py`, not `test_number.py`. The score describes a test *file*, not the function's whole suite |

Two lessons for anyone using mutation scores:

1. **Report the survivors, not just the number.** `clamp` at 0.80 is more interesting than `metric`
   at 0.75, and only the survivor list shows why.
2. **A score is evidence about the test you passed in.** `intcomma`'s 0.44 says nothing bad about
   humanize. It shows the evaluator was asked about the wrong file. Sentinel evaluates *one
   proposed test* by design, which is the question this scoping answers correctly.

### 2.2 What it took to run on someone else's repository

- **`src/` layout.** The package wasn't importable from the evaluator's sandbox copy, and an
  installed copy would point at the *original* tree, so mutants would never run and every one would
  be recorded as missed. The evaluator now puts `<repo>/src` first on `PYTHONPATH` (`lib/pyexec.py`),
  with a self-check that proves the sandbox copy is the one imported.
- **Build-time generated modules.** humanize imports `_version.py`, which `hatch-vcs` writes at
  build time and a plain clone lacks. The trial wrote a one-line stub. A repository with generated
  code has to be built before it can be evaluated, and the README says so.
- **Specification check: silent, correctly.** None of these docstrings states a numeric rate, so the
  documented-rate check had nothing to compare and said nothing. It's designed to stay quiet rather
  than guess.

---

## 3. Reproduce

```bash
# review pipeline (needs NIM, the Neuro-SAN server and the Gateway)
PYTHONPATH=. SENTINEL_TOKEN=<admin> python scripts/run_repo.py https://github.com/OWASP/NodeGoat --full

# evaluator (no model, no server)
git clone https://github.com/python-humanize/humanize
echo '__version__ = version = "0+trial"' > humanize/src/humanize/_version.py
PYTHONPATH=. python -m lib.evaluate --repo humanize --target src/humanize/number.py \
    --function clamp --test humanize/tests/test_number.py --max-mutants 20
```
