# Model evaluation — NIM model selection for Sentinel agents

**Date:** 2026-08-16 · **Author:** Harshit Anand
**Status:** bake-off complete; live A/B on a real repo in progress (§6)

Evidence behind Sentinel's LLM choice. Written because the previous choice died silently and the
project had no record of *why* any model was picked — only *which*. Everything here is reproducible
with the commands in §8.

---

## 1. Why this evaluation happened

On 2026-08-16 the configured primary model returned HTTP 410 on every call:

```
The model 'mistralai/mistral-small-4-119b-2603' has reached its
end of life on 2026-07-27T00:00:00Z
```

The documented fallback, `meta/llama-3.3-70b-instruct`, still times out on the public NIM endpoint
(re-confirmed: 120s, no response). **Both configured models were dead**, so every agent call failed
and the pipeline was non-functional — three weeks before anyone noticed.

**Lesson recorded in `CLAUDE.md` rule 3:** NIM retires models without warning. Re-probe before
trusting any model id.

---

## 2. Catalog probe — what is actually available

`GET https://integrate.api.nvidia.com/v1/models` with the project key returns **102 models**. A
model appearing in that list does **not** mean it is callable — several return 404 for this account
and two are past end-of-life. Probing is the only reliable check.

**Every Sentinel agent calls coded tools**, so structured function-calling is a hard gate: a model
that cannot emit a `tool_calls` response is unusable here regardless of its reasoning quality.

### Disqualified, with cause

| Model | Cause |
|---|---|
| `mistralai/mistral-small-4-119b-2603` | 410 — EOL 2026-07-27 (the former primary) |
| `qwen/qwen2.5-coder-32b-instruct` | 410 — EOL 2026-05-12 |
| `meta/llama-3.3-70b-instruct` | timeout at 120s |
| `openai/gpt-oss-120b` | timeout at 120s |
| `deepseek-ai/deepseek-v4-flash-0731` | 88s on a trivial request; timed out on a real one |
| `mistralai/codestral-22b-instruct-v0.1` | 404 for this account |
| `meta/codellama-70b` | 404 for this account |
| `ibm/granite-34b-code-instruct` | 404 for this account |
| `moonshotai/kimi-k2.6` | 404 for this account |
| `nvidia/llama-3.1-nemotron-ultra-253b-v1` | 404 for this account |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | emits `<TOOLCALL>[...]` as **plain text** — the framework never sees a tool call |
| `nvidia/nvidia-nemotron-nano-9b-v2` | same plain-text `<TOOLCALL>` defect |

> **Note on code-specialist models:** every one on NIM — codestral, codellama, granite-code,
> qwen-coder — is 404 or EOL for this account. "Use a code model for the security reviewer" is not
> an available option. Selection is by general capability tier only.

---

## 3. Bake-off — reviewer quality

### Method

Replays step 3 of the real `security_reviewer_N` prompt (verbatim from `registries/sentinel.hocon`)
over a fixed snippet set, through the real `contract_store` tool-call path. Scored on:

- **recall** — planted vulnerabilities found
- **precision** — findings on benign decoys (false positives)
- **structured** — did it emit a parseable `contract_store` call at all

The 9 planted vulnerabilities are all things **no deterministic rule in `lib/triage` catches** —
path traversal, missing authz, reflected XSS, JS `md5`, unverified JWT, SSRF, `sh -c` injection,
default credential, concatenated SQLi. So recall measures the **LLM layer's own contribution**, not
the deterministic floor's. 5 benign decoys (parameterized query, `bcrypt`, a UUID constant, fixed
`argv`, `textContent`) measure whether a model invents findings.

### Results (production token budget, 2 trials)

| Model | Recall | FPs | Structured | Secs |
|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | 9/9 | 0 | ✓ | 41 |
| `openai/gpt-oss-20b` | 9/9 | 0 | 2/2 | 44 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 9/9 | 0 | 2/2 | 42 |
| `z-ai/glm-5.2` | 9/9 | 0 | 2/2 | 95 |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 4–8.5/9 | 0 | 1–2/2 | 19–42 |
| `stepfun-ai/step-3.7-flash` | 4.5/9 | 0 | 1/2 | 100 |
| `nvidia/nemotron-3-nano-30b-a3b` | 7/9 (needs 16384 tokens) | 0 | ✗ at 8192 | 135 |
| `mistralai/mistral-nemotron` | 7/9 | 1 | **0/2 — never calls the tool** | 23 |

**No model fired on a decoy** (except one mistral-nemotron trial). Precision is not the
differentiator; the ability to produce a structured finding set at all is.

### Methodology correction

An initial run scored `nemotron-3-super` at **0/9**. That was an artifact of the harness capping
`max_tokens` at 4096 — the production config allows 8192. `finish_reason: length` gave it away: the
model reasons out loud in plain content and was truncated before emitting the tool call. At the real
budget it scores 9/9. **The conclusion "the primary is broken" was wrong and is retracted.**

This matters beyond the typo: **verbose reasoning models degrade silently when starved of output
tokens.** No error is raised — the finding set is simply empty. `nemotron-3-super` needs >4096;
`nemotron-3-nano` needs >8192. Do not trim `max_output_tokens` in `config/custom_llm_info.hocon`
without re-running this bake-off.

### The one firm disqualification this produced

`mistralai/mistral-nemotron` was the intended "cheap tier" for the four thin tool-calling agents —
fastest in the catalog (0.5s) and it emitted a clean tool call on a *short* probe. On the real
reviewer prompt it answers in prose and **never calls the tool** (`finish_reason: stop`,
`tool_calls: 0`, at both 8192 and 16384). Shipping it would have silently stopped four agents from
writing their contracts, with no error anywhere.

**Short-prompt probes do not predict long-prompt tool-calling behaviour.** Test at realistic length.

---

## 4. Selected configuration

| Setting | Value |
|---|---|
| Primary | `nvidia/nemotron-3-super-120b-a12b` |
| Fallback | `z-ai/glm-5.2` |
| Alias | `.env` `MODEL_NAME=nvidia-nemotron-3-super` |
| Light slot | `meta/llama-3.1-8b-instruct` (probed, alive) |

Verified live via `scripts/verify_b4.py` — both demo runs pass, decisions correct, contracts
persisted and DB-confirmed.

---

## 5. Reliability observations

- **Provider 500s are real.** NIM returned `HTTP 500 Internal server error` under 8-way concurrent
  probing, and again mid-run during a NodeGoat audit — which **failed the whole run**.
- **The fallback chain does not retry.** `fallbacks` selects a model at session start; it does not
  catch a mid-run provider error. A transient NIM 500 currently fails a gate check rather than
  retrying. This is a gap worth closing independently of model choice.
- `nemotron-3-super` has thrown a 500 twice; `glm-5.2` has not thrown one in any run. n is small —
  this is an observation, not yet a conclusion.
- Concurrency aggravates it. The security-review fan-out runs **sequentially** today (deferred per
  `07 §14`); this is a further argument against un-deferring it.

---

## 6. Live A/B on a real repository — IN PROGRESS

**Repo:** `OWASP/NodeGoat` (deliberately vulnerable, published vuln list → partial ground truth)
**Mode:** `--full` audit, `review_budget_lines: 40` to force a 3–4 shard fan-out.

| | Arm A — central | Arm B — per-agent split |
|---|---|---|
| All agents | `nemotron-3-super` | frontman `nemotron-3-super` |
| Reviewers 1–4 | ” | `glm-5.2` |
| Senior + risk scoring | ” | `nemotron-3-ultra-550b` |
| Quality + thin tool-callers | ” | `gpt-oss-20b` |

**What this can prove:** whether per-agent `llm_config` works in HOCON at all; whether every agent
still writes its contract on a long real chain; latency cost; and — since the deterministic floor is
byte-identical in both arms — a clean read on `source: llm` findings only.

**What it cannot prove:** which arm is "better" in the abstract. A real repo has no ground-truth
label set, and *more findings is not better* — it may be more false positives. NodeGoat's published
vulnerabilities allow spot-checking whether extra findings are real.

**n = 1 per arm.** Trial-to-trial variance in §3 was large (one model swung 8.5 → 4 recall between
identical trials). Treat a single-run delta as directional at best.

### Arm A, attempt 1 — failed

Died 60s in on a provider `HTTP 500`, in `analyzing`. Not a configuration fault. Recorded here
because it is the reliability evidence in §5, not a footnote.

### Arm A, attempt 2 — completed, and it found the real problem

`run_id 1d575610` · 592s · `escalate` / risk 100 / 6 criticals · health 0

| Metric | Value |
|---|---|
| Findings total | 24 |
| **by source** | **`tool`: 24 · `llm`: 0** |
| by severity | critical 6 · high 17 · medium 1 |
| Coverage | 1 shard, 40 of 6861 added lines deep-reviewed, deterministic 100% |

**Every finding came from the deterministic floor. The LLM review layer contributed nothing** — on
OWASP/NodeGoat, an app whose entire purpose is to contain vulnerabilities.

The server log gives the cause:

```
Calling secret_scanner → dependency_cve → Got exception in RunContextRunnable.
                                          Error: Timeout on reading data from socket
Calling complexity_metrics            → Got exception ... Timeout on reading data from socket
```

**Both LLM review agents — `security_reviewer_1` and `code_quality_agent` — timed out mid-call.**
They fetched their tool results, then the NIM request hung and the agent died before writing its
contract. `report_publisher` merged what existed, which was the deterministic findings alone.

#### Why the bake-off missed this

§3 fed snippets **directly** to the model. The real reviewer calls `secret_scanner` first and
reasons over its returned payload — tool findings plus up to `review_budget_lines` snippets with
context. `nemotron-3-super` handles the former in 41s and **hangs on the latter**. Same model, same
token budget, opposite outcome.

This is distinct from the §3 truncation artifact and does **not** retract the 9/9 score: both are
real, and they measure different things. The lesson is that **a model benchmark that skips the
tool-call round trip does not predict in-pipeline behaviour.** Any future model change must be
validated on a real repo run, not a fixture.

#### Retroactive scope

This is the second observation of zero LLM findings on this model (`verify_b4` was the first, where
it was written off as a 5-line fixture having nothing left to find). With NodeGoat as a second data
point, the honest reading is that **the LLM review layer has been non-functional under the central
setup**, and the deterministic floor has been carrying the product. Prior runs' finding counts
should be read as floor-only.

### Arm B — per-agent split — completed

`run_id 617cfc5b` · 787s · `escalate` / risk 100 / 6 criticals · health 0
11 agents overridden, server restarted clean (no alias-resolution errors), same repo and flags.

### Result: identical output, 33% slower

| | Arm A — central | Arm B — split |
|---|---|---|
| Decision / risk / criticals | escalate / 100 / 6 | escalate / 100 / 6 |
| Findings total | 24 | 24 |
| **by source** | tool 24 · **llm 0** | tool 24 · **llm 0** |
| by severity | critical 6 · high 17 · medium 1 | critical 6 · high 17 · medium 1 |
| Wall clock | **592s** | **787s** |

**The per-agent split changed nothing except making the run a third slower.** The prediction stated
before the run held: Arm B was also zero, so the cause is not model choice.

`glm-5.2` failed exactly where `nemotron-3-super` did — and retried once before dying:

```
secret_scanner → dependency_cve → Timeout on reading data from socket
secret_scanner → dependency_cve → Timeout on reading data from socket   (retry)
complexity_metrics             → Timeout on reading data from socket
```

### Root cause: unbounded tool results, not the model

`secret_scanner` **is** bounded — a prior fix caps what it hands the LLM, after an incident where it
"blew past the model's 262k-token context → HTTP 400 → run failed" (its own source comment).

`dependency_cve_tool` and `complexity_metrics_tool` have **no cap at all**. In `--full` audit mode
they return whole-repo-sized payloads: every dependency advisory for NodeGoat's `package.json`,
complexity metrics across 6861 added lines. The reviewer's LLM call stalls on that context and the
socket read times out before the agent can call `contract_store`.

Reproduced on two different models from two different vendors. **This is a Sentinel bug, not a model
deficiency** — and it is the single reason the LLM review layer produces nothing.

**Fix (not yet applied):** cap `dependency_cve` and `complexity_metrics` returns the same way
`secret_scanner` already caps its own, keeping the full unbounded result in code for the
deterministic floor. This is the same rule-4 pattern already used elsewhere: the LLM gets a bounded
view, code keeps the complete data.

---

## 7. Recommendation

### Do not adopt the per-agent split

Identical findings, identical decision, **33% slower** (787s vs 592s). It buys nothing measurable
and adds eleven `llm_config` blocks of config surface plus a second failure mode. The split has been
reverted; `apply_split.py on` re-applies it in seconds if a future model change justifies revisiting.

The §3 bake-off had already pointed this way, but for reasoning that Arm A invalidated — a
fixture-only comparison was never sufficient grounds for this decision in either direction. The A/B
was worth running: it is what produced the finding below.

### Keep the central setup

Primary `nvidia/nemotron-3-super-120b-a12b`, fallback `z-ai/glm-5.2`. Verified live end to end.

### What this evaluation actually found

1. **The configured primary was dead** (410, EOL 2026-07-27) — the pipeline had been non-functional
   for three weeks with no alert.
2. **`mistral-nemotron` never calls tools on long prompts** — the intended cheap tier for four
   agents would have silently stopped them writing contracts.
3. **The LLM review layer produces nothing on real repos**, on any model, because
   `dependency_cve` and `complexity_metrics` return unbounded payloads that stall the call until the
   socket times out. The deterministic floor has been carrying the product. ← *the important one*
4. **`fallbacks` does not retry.** A provider `HTTP 500` mid-run fails the whole run; for a gate
   meant to block merges, a transient NIM error currently means a failed check rather than a retry.

Items 3 and 4 are correctness bugs, not model preferences. **Neither is fixed by model selection**,
which is the main lesson of this exercise: the question asked was "which model per agent", and the
answer turned out to be "the models were never the problem".

### Suggested order of work

1. Cap `dependency_cve` + `complexity_metrics` LLM-facing returns (§6) — unblocks the review layer.
2. Re-run the NodeGoat audit and confirm `source: llm` findings appear.
3. Add retry-on-provider-error around the agent LLM call (§5).
4. Only then revisit per-agent models, if the split still looks attractive.

---

## 8. Reproduction

```bash
# catalog
curl -s https://integrate.api.nvidia.com/v1/models -H "Authorization: Bearer $NVIDIA_API_KEY"

# live pipeline check (both demo runs, DB-confirmed)
PYTHONPATH=. python -u scripts/verify_b4.py

# real-repo audit through the Gateway
PYTHONPATH=. SENTINEL_TOKEN=<admin> python -u scripts/run_repo.py \
    https://github.com/OWASP/NodeGoat --full
```

The bake-off harness and the per-agent split patcher live in the session scratchpad; both are
single-file and self-contained. Re-create from §3 if this needs re-running — the fixture is the
valuable part, not the harness.
