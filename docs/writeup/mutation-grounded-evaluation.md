# A passing test proves nothing: grading AI-generated tests by experiment

*How Sentinel decides whether a generated test is worth a human's attention, and the hole we found
in our own answer.*

---

## The problem

Ask a model to write a test and you get one that passes. That's the easy part. A test can pass
while it:

- asserts `add(1, 2) == 3` and never reaches the branch it was written for,
- asserts nothing at all (`assert True`, `assert result is not None`),
- or asserts the **bug**, because the model read the implementation and wrote down what it does.

A green checkmark distinguishes none of these. Asking a second model "is this a good test?" just
moves the question onto another opinion. We wanted a verdict that comes from an **experiment**: one
that anyone can re-run and get the same number, that a model can't talk its way past, and that
costs nothing beyond running the test a few more times.

## Part 1: mutation testing as the grader

The experiment is old and well understood. It's mutation testing, applied to one test and one
function at a time:

1. **The test must pass against the unmodified code.** A test that's red on correct code is worse
   than no test, because it teaches people to ignore red.
2. **Break the function on purpose,** one small edit at a time, and re-run the test. Each edit is a
   *mutant*:

   | operator   | edit                     | the bug it models              |
   |------------|--------------------------|--------------------------------|
   | comparison | `<` → `<=`               | off-by-one in a boundary check |
   | boundary   | `10` → `11`              | the same bug in the literal    |
   | logic      | `and` ↔ `or`             | a mishandled condition         |
   | negation   | `return x` → `return not x` | an inverted result          |
   | arithmetic | `+` → `-`, `*` → `//`    | a sign or operator slip        |
   | statement  | drop a statement         | a missing guard or side effect |

3. **Mutation score = mutants caught / mutants generated.** Every mutant the test fails on is a
   real bug it would have caught. Every mutant it misses is listed by line and edit, and that list
   is often more useful to a reviewer than the score.
4. **Reject tautologies before spending anything on them.** If no assertion mentions a name, a call
   or an attribute, the test is rejected structurally.

Three engineering details turned out to matter more than the operator list:

- **One edit per mutant, generated deterministically.** The same source always yields the same
  mutants in the same order, so a score is reproducible and two runs can be compared. Mutants are
  built by rewriting the Python AST and unparsing it, so every mutant is guaranteed to be valid
  Python. That guarantee is also why the method is Python-only for now: there's no equivalently
  safe round-trip for JavaScript.
- **Scope to the function under test.** A campaign is about 10–20 runs of one test file, not a
  sweep of the whole suite, so it stays fast enough to run on every proposal.
- **Never touch the repository.** Each evaluation copies the workspace into a temporary directory,
  mutates the copy, and deletes it afterwards.

### Does the score discriminate?

We graded three tests for the same function, written to be progressively weaker:

| test                                         | score    | verdict  |
|----------------------------------------------|----------|----------|
| thorough: boundary, both branches, member flag | **1.00** | accepted |
| happy path only                              | 0.25     | rejected |
| "returns a number" type check                | 0.12     | rejected |

All three tests pass. Only one of them is evidence of anything, and the score puts that spread on
a single line.

### The bug that would have quietly deflated every score

Python decides whether its bytecode cache is stale from the source file's **modification time and
size**. Rewrite a file with an edit of the same length within the same second (`0.90` → `0.85`,
`10` → `11`) and the interpreter runs the cached bytecode of the code you just replaced.

That's exactly what a mutation campaign does, many times per second. A mutant that never actually
ran lets the test pass, so it gets recorded as **missed**, and the score drops for reasons that have
nothing to do with the test. We caught it because a demo's "corrected" function kept returning the
buggy value. Reproduced directly, a module rewritten to return `85.0` printed `90.0`.

The fix is two environment variables on every subprocess that runs freshly written code:
`PYTHONPYCACHEPREFIX` pointing at a new directory each time, and `PYTHONDONTWRITEBYTECODE=1`. If you
build a mutation tester in Python, check for this first. Ours had published numbers before we found
it. Re-measuring gave the same 1.00 / 0.25 / 0.12, so the bug was latent, but it wouldn't have
stayed that way.

## Part 2: the hole — mutation testing certified a bug

We gave the evaluator this function:

```python
def loyalty_discount(total, years):
    """Members of 3+ years get 15% off. Everyone else pays full price."""
    if years >= 3:
        return round(total * 0.90, 2)   # bug: 10%, not 15%
    return total
```

A generated test asserted `loyalty_discount(100.0, 3) == 90.0`, which is the **buggy** output:

```
verdict        : accepted
mutation score : 0.833  (5 of 6 injected bugs caught)
```

Everything behaved as designed, and the result is a test that permanently enforces the bug. The
day someone fixes the discount, this test goes red and reports the *fix* as the breakage.

This can't be patched, because it isn't a defect in the mutation tester. Mutation testing measures
**sensitivity**: if I break the code, does the test notice? It can't measure **correctness**: is
what the test expects actually right? When the test and the implementation are wrong in the same
way, they agree, and agreement is all mutation testing can see. The implementation is its only
witness.

The research literature has the right terms for this. A **regression oracle** asserts what the code
does, and it can't catch a bug that's already there. A **specification oracle** asserts what the
code should do. LLM-generated tests are regression oracles by default, the same limitation classic
test generators like EvoSuite and Randoop have ([arXiv 2410.21136](https://arxiv.org/abs/2410.21136)).

So the first thing to fix wasn't a model. It was **the claim**. "The test agrees with the code" had
been presented as "the test is correct".

## Part 3: independent witnesses

The design rule we adopted:

> **The implementation must never be the only witness to its own correctness.**

A verdict is now a *classification* that names which independent witnesses agreed, not one number.

| witness | how it is checked | needs a model? |
|---|---|---|
| **the implementation** (mutation score) | the experiment above, which measures sensitivity only | no |
| **the other commit** (differential) | run the test against a `git worktree` of the change's base commit | no |
| **the documentation** (specification check) | a docstring that states "15% off" implies a multiplier of 0.85; a function that scales by 0.90 contradicts it | no |
| **stated intent** (blind oracle) | a separate model reads only the signature, docstring and PR text, with the **function body redacted**, and predicts outputs; *code* then runs the real function against those predictions | one call |

The **differential** witness is nearly free once you have both commits:

- *passes at base and head*: the test locks behaviour that predates this change, so it can't have
  encoded a bug this change introduced. → `regression_guard`
- *passes at head, fails at base*: the test asserts behaviour this change altered. That's the
  reviewer's actual question, now asked precisely. → `change_documented`
- *function absent at base*: new code, with nothing to compare against. → `characterization`

The **blind oracle** has one rule that does all the work: the model stating what the code should do
must never see what the code does. A model shown the implementation restates it. So the module that
assembles its input strips the target body out of the source, and a unit test fails if that
redaction ever leaks the implementation. The model doesn't grade anything either. It states
expected cases, and a coded tool executes them. We learned this one the hard way. In early live
runs the agent *asserted* a verdict on all 19 calls, and the tool never once performed the
comparison. Now an agent's claim with no measurement behind it is ignored.

We also learned that **thin intent has to read as no intent**. With a one-word PR title and no
docstring, the oracle confabulated: it returned `disputed` on one run in three against correct code.
Because a dispute outranks every other signal, a guess turns into a false alarm. Below a length
floor for docstring and description, the oracle now stays silent.

The resulting classifications:

| classification | meaning | adoptable? |
|---|---|---|
| `intent_confirmed` | the blind oracle's predictions match the code | yes, the strongest kind |
| `regression_guard` | the behaviour predates the change and the test is sensitive | yes, labelled "locks existing behaviour" |
| `change_documented` | the test asserts behaviour this change altered | yes, after the author confirms intent |
| `characterization` | nothing independent vouched for it | yes, labelled *unverified, bugs included* |
| **`disputed`** | the code contradicts its own documentation | **no.** It's reported as a finding on the **code** |

The last row is the reframing that turned a limitation into a feature:

> **A generated test that disagrees with intent is worth more than one that agrees.**

The `loyalty_discount` case now ends like this:

```
before   accepted · mutation score 0.83 · adoptable as a verified test
after    disputed · "the docstring states 15%, which implies a multiplier of 0.85,
                     but loyalty_discount scales by 0.9" · not adoptable
                   · reported as a medium finding on the code under review
```

The finding never says *which* side is wrong. The docstring may be the stale one. That call belongs
to a human, and the output names both sides.

## Part 4: on real code

Fixtures are written to be caught, so we ran the evaluator on code nobody wrote for it: nine
functions of [`python-humanize/humanize`](https://github.com/python-humanize/humanize), each graded
against its maintainers' own test file with 20 mutants per function.

| | |
|---|---|
| functions | 9 |
| scored 1.00 | 3 (`intword`, `apnumber`, `scientific`) |
| median score | **0.90** |
| lowest | `intcomma` 0.44 |
| cost | 30–110 s per function |

The score is the headline. The **surviving mutants** are the product:

- **`clamp`, 0.80: a real missing test, confirmed by execution.** Change `value < floor` to
  `value <= floor` and `clamp(0.01, floor=0.01)` returns `'<0.01'` instead of `'0.01'`. None of the
  eleven test cases puts a value exactly on the boundary. That's a one-line test a maintainer can
  add today, found without a model.
- **`ordinal`, 0.90: an equivalent mutant.** Flipping `gender == "male"` changes nothing in
  English, where ordinal suffixes don't depend on gender. No test *can* kill it, which is a known
  limit of mutation testing, and the survivor list makes it visible instead of silently lowering
  confidence in a good test.
- **`intcomma`, 0.44: the wrong file.** Every survivor sits in the locale-translation branch, which
  humanize tests in `test_i18n.py`. A mutation score describes the test you passed in, not the
  function's whole suite. For grading one *proposed* test, which is Sentinel's use, that scoping is
  exactly right. For auditing a suite, it's a trap.

Running on someone else's repository also exposed two practical requirements. A `src/`-layout
package must be imported from the sandbox copy (an installed copy points at the original tree, so
mutants would silently never run). And build-time generated modules, such as a `hatch-vcs`
`_version.py`, must exist before evaluation. Full results, including the review pipeline on four
real repositories: [real-repo-trials.md](real-repo-trials.md).

## What it cannot do

The oracle problem is undecidable in general: nothing can know what code *should* do without being
told. Concretely:

- The blind oracle is silent on undocumented functions, by design.
- The differential witness says nothing about bugs older than the diff.
- The specification check reads numeric rate claims only. Prose like "returns the newest entry" is
  the blind oracle's job, and that's a model call that can be wrong. The state of the art in
  documentation-derived oracles ([AugmenTest](https://arxiv.org/pdf/2501.17461)) reports about 30%
  success at its strictest threshold.
- Mutation testing measures the operators you implement. Six classic operators model common slips,
  not missing features or wrong algorithms.

What the design fixes permanently is narrower, and real. **The system stops presenting "the test
agrees with the code" as "the test is correct", and it surfaces every disagreement it can detect
instead of silently resolving it in the code's favour.** That's a property of the design, not a
capability of a model, which is why it holds.

## Try it

The evaluator runs on its own, with no agents, no database and no model, on any Python repository
that pytest can run:

```bash
# from a Sentinel checkout (PYTHONPATH=.)
python -m lib.evaluate --repo path/to/repo --target pkg/module.py \
    --function my_function --test path/to/test_module.py [--base <sha>] [--json]
```

It prints the verdict, the classification and every mutant the test missed. The exit status is 0
only for an accepted, undisputed test, so a CI step can gate on it.
