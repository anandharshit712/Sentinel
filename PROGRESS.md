# Sentinel — build tracker

**The answer to "where are we?" without having to ask.** Updated whenever a phase closes (see
`CLAUDE.md` rule 0d). Last updated **2026-09-26** · HEAD `342c2a6` · 175 tests · 69 commits.

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
| P3 — test generation | 🟡 one measurement owed to provider health | 95% |
| P4 — learning loop | ✅ complete | 100% |
| Oracle evidence (doc 11) | ✅ complete | 100% |
| P5 — polish & showcase | 🟡 dashboard done, writeup not started | 30% |
| P7 — production track | ⬜ deliberately deferred | 0% |

**Inventory:** 27 coded tools · 13 agents across 2 networks · 16 lib modules · 10 verification
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

---

## 3. P2 — full review cluster ([08](docs/solution/08-post-hackathon-plan.md))

| # | Item | State | Notes |
|---|---|---|---|
| P2.1 | `performance_findings` / `compliance_findings` contracts | ✅ | |
| P2.2 | `lib/perf_rules.py` | ✅ | real loop/async context, not flat regex |
| P2.3 | `lib/license_rules.py` | ✅ | |
| P2.4 | `performance_scanner` | ✅ | |
| P2.5 | `license_scanner` | 🟡 | **Left: resolving each dependency's licence.** Needs a registry lookup per dependency with caching and offline handling. The tool says so in its output rather than implying an audit it did not do |
| P2.6 | Two reviewer agents in the network | ✅ | |
| P2.7 | Four-dimension merge in `report_publisher` | ✅ | |
| P2.8 | Gateway + SPA | ✅ | |
| P2.9 | `verify_p2.py` | ✅ | **3/3** |
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
| P3.10 | `verify_p3.py` **3/3** | 🟡 | **Best measured 2/3** (a NIM `HTTP 500` mid-loop; completed trials scored 1.00). The fixture was also **faking its base SHA**, so the differential tier reported `unknown` on every live run — fixed 2026-09-26, but **the fixed fixture has not yet had a clean live run**: the machine lost outbound connectivity mid-attempt (`WinError 10060`, the NIM endpoint unreachable by plain curl). **Why still open: connectivity, not code.** One command when the network is back |
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
| Public writeup of the mutation-grounded method | ⬜ | The most publishable piece of the project, per the vision doc |
| Trial against real open-source repos | 🟡 | NodeGoat, ORION, Accident_Detection_System have all been run; **left: writing up what was found** |
| Extract the evaluation layer as a standalone tool | ⬜ | |

## 8. Open items, and why each is still open

| Item | Why it is not done | What unblocks it |
|---|---|---|
| `verify_p3` at 2/3 | A NIM `HTTP 500` mid-loop (primary fails ~1 call in 12; the key rotation did not change it). The re-run with the corrected fixture was then blocked by this machine losing outbound connectivity entirely | Re-run when the network and NIM are both up |
| Differential tier, live | Verified by unit tests and `verify_o.py`, but not yet through a live generation run: the fixture that would exercise it was only fixed today and the network dropped before it completed | The same single `verify_p3` run |
| LLM-reviewer contribution | Unknown whether reviewers add findings beyond the deterministic floor now that `contract_store` accepts their writes | A per-agent bake-off on a stable provider day |
| C3 webhook route | Superseded for the demo by the GitHub Action | Only needed for real webhook intake (P7) |
| Dependency licence resolution | Needs per-dependency registry lookups | A decision to take on network calls inside a coded tool |
| P5 writeup | Not started | Time; no technical blocker |

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
PYTHONPATH=. python scripts/probe_models.py    # is NIM healthy right now?
PYTHONPATH=. python scripts/verify_p2.py 3     # review cluster      needs the network up
PYTHONPATH=. python scripts/verify_p3.py 3     # test generation     needs the network up
PYTHONPATH=. python scripts/verify_b4.py       # the two demo runs   needs the network up
python -m pytest -q                            # 173 unit tests
```

The first two and the probe run on any day. The rest need NIM to be answering.
