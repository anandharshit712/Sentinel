# Sentinel — build tracker

**The answer to "where are we?" without having to ask.** Updated whenever a phase closes (see
`CLAUDE.md` rule 0d). Last updated **2026-11-20** · 201 tests.

Status vocabulary, used strictly:

| | meaning |
|---|---|
| ✅ **done** | built, tested, and verified by something that runs |
| 🟡 **partial** | usable, but something named below is missing — the row says **what** and **why** |
| ⬜ **not started** | no code |
| 🚫 **out of scope** | deliberately excluded, with the reason |

A row is never ✅ because the code exists. It is ✅ when something proves it works.

---

## 1. Where the project stands

| Track | State | % |
|---|---|---|
| Hackathon build (M0–M5) | ✅ delivered, demo-ready, browser-verified | 97% |
| P2 — full review cluster | ✅ complete | 100% |
| P3 — test generation | ✅ complete (`verify_p3` 3/3, 2026-09-26) | 100% |
| P4 — learning loop | ✅ complete | 100% |
| Oracle evidence (doc 11) | ✅ complete | 100% |
| P5 — polish & showcase | ✅ complete | 100% |
| Platform track ([12](docs/solution/12-platform-plan.md)) | 🟡 in progress: Phase 1 done, Phases 2–8 open | 13% |
| P7 — production track | ⬜ deliberately deferred | 0% |

**Inventory:** 27 coded tools · 13 agents across 2 networks · 17 lib modules · 10 verification
scripts · 4 migrations · 12 design documents.

---

## 2. Hackathon build

| # | Item | State | If partial: what's left and why |
|---|---|---|---|
| 0.1 | Repo scaffold per 04 §1 | ✅ | |
| 0.2 | `deploy/docker-compose.yaml` | 🚫 | Host-native dev was chosen instead (rule 3). Compose/K8s belong to P7 packaging; no `deploy/` dir exists |
| 0.3 | Sample repos (Python + Node) | ✅ | |
| 0.4 | Framework spike | ✅ | findings in 07 §3.1 |
| 0.5 | Tracer bullet → **M0** | ✅ | |
| 1 | Contracts, DB, config → **M1** | ✅ | 12 contracts, 4 migrations |
| A1–A8 | All coded tools | ✅ | |
| B1–B5 | Network slices → **M2, M3** | ✅ | |
| C1 | Gateway app, state machine, simulate | ✅ | |
| C2 | Invoker (streaming, MAXIMAL filter) | ✅ | |
| C3 | GitHub adapter | 🟡 | **Left: the HMAC webhook route and PR-comment posting.** The GitHub Action posts to `/simulate` instead, which covers the demo path, so a second intake route was never needed. Real webhooks are P7 |
| C4 | REST + SSE, approvals, rerun, audit | ✅ | |
| D1–D5 | Dashboard → **M4** | ✅ | |
| 6.1–6.6 | Integration, demo scripts, rehearsal → **M5** | ✅ | browser-verified, 31 UI checks |
| — | Gateway tests no longer reap live runs | ✅ | Fixed 2026-10-09. `TestClient` fires the startup hook, which failed every in-flight run in the **real** dev DB. Running pytest killed a live ORION audit, and the audit row went to the fake DB, so it left no trace |

---

## 3. P2 — full review cluster ([08](docs/solution/08-post-hackathon-plan.md))

| # | Item | State | Notes |
|---|---|---|---|
| P2.1 | `performance_findings` / `compliance_findings` contracts | ✅ | |
| P2.2 | `lib/perf_rules.py` | ✅ | real loop/async context, not flat regex |
| P2.3 | `lib/license_rules.py` | ✅ | |
| P2.4 | `performance_scanner` | ✅ | |
| P2.5 | `license_scanner` | ✅ | Dependency licences resolved since 2026-11-20 (platform Phase 7): PyPI/npm lookups, cache, offline → `unresolved` (never clean) |
| P2.6 | Two reviewer agents in the network | ✅ | |
| P2.7 | Four-dimension merge in `report_publisher` | ✅ | |
| P2.8 | Gateway + SPA | ✅ | |
| P2.9 | `verify_p2.py` | ✅ | **3/3** |
| P2.10 | LLM-facing payloads bounded | ✅ | Added 2026-10-09. On a 63k-line ORION audit `dependency_graph` handed the LLM the whole change profile (~830k tokens, a `400` context overflow), `secret_scanner` snippets came to ~140k tokens, and reviewers were told to **retype** every tool finding into their contract, which ran past the socket timeout. Now: a profile summary (~1k), a 120k-char snippet budget with honest coverage, the top 40 complexity rows, and reviewers store only their own findings. Measured in [real-repo-trials §1.3](docs/writeup/real-repo-trials.md) |
| P2.11 | Dependency advisories in the deterministic floor | ✅ | Added 2026-10-09. They used to reach the report only if a reviewer LLM transcribed them. `_dedup` also stopped collapsing two advisories on one manifest (no line number) into one finding |
| — | Java / Go / Rust | 🚫 | Per-language grammar + import resolution + a test-runner adapter each. A phase of its own, not a config flag |

---

## 4. P3 — test generation ([09](docs/solution/09-test-generation-plan.md))

| # | Item | State | Notes |
|---|---|---|---|
| P3.1 | `lib/coverage_parse.py` | ✅ | coverage.py JSON, Cobertura, lcov |
| P3.2 | Coverage collection in `test_runner` | ✅ | opt-in; absent plugin changes nothing |
| P3.3 | `coverage_gap` inside `finalize_run` | ✅ | body lines only — the `def` line executes at import and would disguise every untested function as "partial" |
| P3.4 | `coverage_gaps` contract + table | ✅ | |
| P3.5 | `lib/mutate.py` | ✅ | single-edit, reproducible |
| P3.6 | `test_evaluator` | ✅ | measured **1.00 / 0.25 / 0.12** across thorough, happy-path, type-check tests |
| P3.7 | `sentinel_testgen` network | ✅ | off the promotion chain by design |
| P3.8 | `test_proposals` table | ✅ | migration 0003 |
| P3.9 | Gateway endpoint + dashboard card | ✅ | |
| P3.10 | `verify_p3.py` **3/3** | ✅ | **3/3 on 2026-09-26**, every trial 1.00 with 8/8 mutants caught, and the differential tier exercised live for the first time (`changed_behavior`) now that the fixture makes two real commits instead of passing a fabricated base SHA |
| — | LLM critique layer | ✅ | Superseded and delivered by doc 11 (blind oracle) |
| — | JS/TS generation | 🚫 | `lib/mutate` needs `ast.unparse` for a guaranteed round-trip; tree-sitter has no unparse, so JS mutation is byte-range surgery plus JS-specific operators (`==` vs `===`, truthiness) each with equivalent-mutant risk |

---

## 5. P4 — learning loop ([10](docs/solution/10-learning-loop-plan.md))

| # | Item | State | Notes |
|---|---|---|---|
| P4.1 | `lib/calibration.py` | ✅ | no rate below n=5; unrecorded outcome is `unknown`, never `clean` |
| P4.2 | `POST /runs/{id}/outcome` | ✅ | |
| P4.3 | `GET /api/v1/calibration` + CLI | ✅ | |
| P4.4 | Recommendations | ✅ | **advice only** — decided 2026-09-24; a gate that re-tunes itself has no stable meaning |
| P4.5 | Calibration screen | ✅ | renders on an empty database |
| P4.6 | `verify_p4.py` | ✅ | **12/12** |

---

## 6. Oracle evidence ([11](docs/solution/11-oracle-evidence-plan.md))

Closed the hole where a test asserting a bug scored 0.83 and read "accepted".

| # | Item | State | Notes |
|---|---|---|---|
| O1 | `lib/differential.py` (Tier 1) | ✅ | base-commit worktree; unchanged / changed / new / unknown |
| O2 | Classification replaces the bare verdict | ✅ | |
| O3 | Evaluator integration | ✅ | |
| O4 | `lib/spec_check.py` (Tier 3) | 🟡 | **Left: invariants beyond documented rates** (bounds, idempotence, type preservation). The rate check covers the demonstrated failure with a far lower false-positive surface; broader invariants were deferred rather than guessed at |
| O5 | Blind oracle agent (Tier 2) | ✅ | Verified live 2026-09-26: the agent ran, returned `confirms_intent`, and its verdict was persisted with all four evidence tiers (proposal 8, score 1.00, 8/8 mutants caught) |
| O8 | `intent_confirmed` classification | ✅ | Unplanned, found by that live run: a confirmed intent was being reported as "characterization — unverified", understating an independent witness |
| O6 | `spec_implementation_mismatch` finding | ✅ | reported on every run, not just when a test was asked for |
| O7 | Dashboard + `verify_o.py` | ✅ | **17/17**, no LLM, no network |
| — | `lib/pyexec.py` | ✅ | Unplanned. Python caches bytecode on (mtime, size), so a same-size mutant written within a second ran the code it replaced — a latent way to deflate every mutation score |

---

## 7. P5 — polish & showcase

| Item | State | Notes |
|---|---|---|
| Dashboard for reasoning trails + generated tests | ✅ | |
| Public writeup of the mutation-grounded method | ✅ | [docs/writeup/mutation-grounded-evaluation.md](docs/writeup/mutation-grounded-evaluation.md). In the repo, not yet posted anywhere public; where to post it is the author's call |
| Trial against real open-source repos | ✅ | [docs/writeup/real-repo-trials.md](docs/writeup/real-repo-trials.md): the review pipeline on 4 repos and the evaluator on 9 `humanize` functions (median 0.90). Found a real missing boundary test in `humanize.clamp`, confirmed by execution |
| Extract the evaluation layer as a standalone tool | ✅ | `python -m lib.evaluate`: no Neuro-SAN, no DB, no model. `test_evaluator` is now a thin wrapper. Handles `src/` layouts. Exit 0 only for an accepted, undisputed test. Self-check: `python -m lib.evaluate --self-check`. Not packaged for pip: nothing has asked for it yet |

## 7b. Platform track ([12](docs/solution/12-platform-plan.md))

| # | Item | State | Notes |
|---|---|---|---|
| 0 | Model health | ✅ | 2026-11-14: chain `nemotron-3-ultra` → `mistral-large-4-1t-a52b` → `gpt-oss-20b`, probe 3/3, `verify_b4` PASS |
| 1 | Measure review quality | ✅ | 2026-11-20. Findings stored as rows (migration 0005), human verdicts, `missed_bug` outcome, precision by source × dimension / repo / version, `runs.sentinel_version`. `verify_p1m` 11/11 (real DB, no LLM); browser check 7/7 (a verdict and its note persist across a reload, "previously marked" shown, Calibration shows precision + missed bugs). No real precision numbers yet: that needs real runs judged by a person |
| 2 | Per-run sandbox | 🟡 | **Built, Docker gates not run.** `lib/sandbox` choke point (local + docker), `sandboxd`, sandbox image, escape fixture + `verify_sandbox.py`, all repo-executing call sites routed through it, Gateway calls `open_run`/`close_run`, clone `core.symlinks=false`, blind-oracle env now scrubbed. Unit tests pass in both modes (docker mode against the real sandboxd with the docker CLI faked). **Left: the escape suite and `verify_b4`/`verify_p3` in `SANDBOX_MODE=docker` — the Docker engine was not running on 2026-11-20.** gVisor (`runsc`) is configurable but provable only on a Linux host |
| 3 | Packaging (Docker) | ⬜ | |
| 4 | Accounts, tenancy, BYOK | 🟡 | **Built; DB isolation and BYOK proven; Keycloak not run yet.** Migrations 0006 (accounts/identities/repos/keys/llm_keys) + 0007 (FORCE row-level security, deny-by-default scope, account-filling trigger); Gateway OIDC sign-in (BFF cookies + refresh), link gate, repo ownership (GitHub admin / GitLab Maintainer / `.sentinel-verify`), CI auth (OIDC job tokens + hashed per-repo API keys, `POST /api/v1/ci/runs`), quotas, suspend/revoke, 90-day retention purge, privacy page, Settings/Admin screens. `test_tenancy.py` 23/23 incl. real-DB RLS (A cannot read or write B's rows by direct id). **`verify_byok.py` PASS live** (no key → refused even with the server's env key; key in sly_data → promote; key in no log/thinking file). **Left: `verify_tenancy.py` and the realm import need Keycloak (Docker not running on 2026-11-20); live GitHub/GitLab linking needs the user's OAuth apps** |
| 5 | CI platforms | ⬜ | |
| 6 | Test generation in CI | ⬜ | |
| 7 | Licence resolution, `spec_check` invariants, proposals 9–14 | ✅ | 2026-11-20. Licence lookup (PyPI/npm, 30-lookup budget, 7-day cache, unresolved ≠ clean, max `medium`, live-checked: pyqt5 → GPL v3 finding, 404 → unresolved). Three `spec_check` invariants, each 0 FP on `humanize` (bounds/idempotence judged nothing there; type preservation 0/29, 13 real annotation bugs found in the wider stdlib scan). Proposals 9–14 relabelled `characterization` after a `pg_dump` backup; old labels kept under `evaluation.reclassified_from`. `test_p7.py` + full suite green |
| 8 | Open-source readiness | ⬜ | |

## 8. Open items, and why each is still open

| Item | Why it is not done | What unblocks it |
|---|---|---|
| Tier 2 verdicts before 2026-09-26 | Every live proposal carried a `confirms_intent` or `disputed` verdict that the **agent asserted** — the oracle tool ran 19 times and never once performed a comparison. Historic proposals 9–14 carry unearned verdicts | Fixed: the tool now records its own measurement and `proposal_store` ignores an unmeasured claim. Old rows are left as they are, with this note |
| Model chain (resolved 2026-11-14) | **Closed.** The probe found the primary `nemotron-3-super` returning HTTP 404 ("Function … Not found for account", though `/v1/models` still lists it) and `glm-5.3-flash` still timing out at 120s. Under the standing rule (probe 3/3 **and** `verify_b4` PASS) the chain is now `nemotron-3-ultra` → `mistral-large-4-1t-a52b` → `gpt-oss-20b`; `.env` `MODEL_NAME` moved to `nvidia-nemotron-3-ultra` (it overrides the whole network, so it was pointing every agent at the dead model). `probe_models.py` 3/3 usable, `verify_b4` PASS (happy → promote, insecure → escalate at risk 100, 1 critical, health 51) | Re-probe before trusting it; NIM health swings hour to hour |
| LLM-reviewer contribution (answered 2026-10-09) | **Answered.** Every security finding on every real repo came from the deterministic floor. On ORION, after the fixes, 2 of 17 LLM findings were real and beyond any rule, 4 were false positives (two at critical), 1 duplicated the floor and 10 restated complexity numbers. Earlier trials show the same shape (e.g. hardcoded Windows dataset paths on Accident_Detection_System). For three months the layer was silently non-functional because of four defects, none of them the model. See [real-repo-trials §1](docs/writeup/real-repo-trials.md) | — |
| C3 webhook route | Superseded for the demo by the GitHub Action | Only needed for real webhook intake (P7) |

## 9. Deliberately excluded

| Item | Reason |
|---|---|
| Java / Go / Rust analysis | Per-language grammar, import resolution and runner adapter each |
| JS/TS test generation | No `ast.unparse` equivalent for a safe mutation round-trip |
| Auto-tuning the risk thresholds | A gate that silently re-tunes itself has no stable meaning, and rare-event feedback drifts towards "promote more" (10 §1) |
| Docker for development | Host-native by decision; compose/K8s reserved for P7 |
| An LLM deciding which files to skip in review | Nondeterministic exclusion in a security gate means silently missed findings |

## 10. How to check any of this yourself

```bash
PYTHONPATH=. python scripts/verify_o.py        # oracle evidence     17/17, no provider needed
PYTHONPATH=. python scripts/verify_p4.py       # learning loop       12/12, no provider needed
PYTHONPATH=. python scripts/verify_p1m.py      # review precision    11/11, no provider needed
PYTHONPATH=. python scripts/probe_models.py    # is NIM healthy right now?
PYTHONPATH=. python scripts/verify_p2.py 3     # review cluster      needs the network up
PYTHONPATH=. python scripts/verify_p3.py 3     # test generation     needs the network up
PYTHONPATH=. python scripts/verify_b4.py       # the two demo runs   needs the network up
PYTHONPATH=. python -m lib.evaluate --self-check  # standalone evaluator, no provider needed
python -m pytest -q                            # 201 unit tests
```

The first two and the probe run on any day. The rest need NIM to be answering.
