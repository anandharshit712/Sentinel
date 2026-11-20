# 12 — Platform plan: measured reviews, sandbox, packaging, tenancy, CI, test generation in CI

Status: in progress (2026-11-20). Documents 01–07 describe the hackathon build and stay frozen;
08–11 cover the post-hackathon phases. This document covers turning Sentinel into something a
stranger can self-host with `docker compose up`, and that can be hosted for others safely.
Deployment itself is out of scope here.

## 1. Decisions this plan implements

| Decision | Consequence |
|---|---|
| Staged product: self-host → open source → hosted | One codebase, two auth modes (`AUTH_MODE=tokens` / `oidc`) |
| Bring your own LLM key (BYOK) | The key travels per run in `sly_data.llm_config`, never in a shared config |
| CI secrets live in the CI platform's own store | OIDC job tokens for GitHub/GitLab, per-repo API key for Jenkins |
| Nothing works until a code host is linked; repo ownership is verified | The link gate (§5) |
| Untrusted repo code runs only in a per-run sandbox container | §3 |
| Platforms: GitHub Actions, GitLab CI, Jenkins | §6 |
| Test generation in CI only for changed functions that have no test | §7 |
| Review quality becomes a measured number | §2 — first, because every later phase should be measured by it |

## 2. Phase 1 — Measure review quality

**Problem.** Sentinel has reported findings for months, but whether they are *right* has only
been judged by reading them. The 2026-10-09 trials put a number on one repo by hand (2 of 17 LLM
findings real and beyond any rule). Nothing recorded those judgements, so there was no way to
compare one version of the reviewer against the next.

**Design.**

- **Findings become rows.** `report_publisher` writes one row per merged finding to the
  `findings` table, which has existed since the baseline but was never written. Migration `0005`
  adds `fingerprint`, `source`, `verdict` (`real` / `false_positive` / `wont_fix`), `verdict_by`,
  `verdict_at` and `note`. The existing `kind` column carries the review dimension (security,
  quality, performance, compliance, spec). Its CHECK constraint is widened rather than adding a
  second column that means the same thing. The report JSON is unchanged apart from two added
  fields per finding (`dimension`, `fingerprint`).
- **Unique ids per run.** Each shard reviewer numbers its findings from `SEC-001`, so ids
  collided after the merge. A repeated id gets a `#n` suffix, so every finding has its own row
  (`UNIQUE (run_id, finding_id)`).
- **Fingerprint** = hash of repo + file + category + title with digits blanked. It deliberately
  leaves out the line number and the tool's id, because both change whenever code above the
  finding moves. Known limit: two findings with the same title in one file share a fingerprint.
  That is acceptable because the fingerprint drives only a display hint.
- **Verdicts never change the gate.** `POST /api/v1/findings/{id}/verdict` (approver) records a
  judgement and an audit event. Re-publishing a report refreshes a finding's content but never
  overwrites its verdict. If the same fingerprint was judged on an earlier run of the same repo,
  the UI says "previously marked …". That is display only: suppressing a finding because it was
  once called a false positive would make the gate depend on history rather than on the code in
  front of it.
- **Missed bugs.** `missed_bug` is a new outcome type on `POST /runs/{id}/outcome` (file, line,
  category, description; the description is required). It is the only window onto recall: a bug
  nobody reported leaves no finding row. It is excluded from promotion calibration, because a
  missed review finding says nothing about whether the promotion went wrong.
- **Precision report.** `lib/calibration.review_precision`:
  precision = (real + won't fix) / judged. Won't-fix counts as a true finding, because the team
  agreed it was real and chose to live with it. Unjudged findings are counted and never assumed
  either way. The same `MIN_SAMPLE = 5` floor applies: below it, no rate. Precision is reported
  three ways: source × dimension (is the LLM layer worth its cost, per dimension?), per repo, and
  per Sentinel version.
- **Version stamp.** `runs.sentinel_version` holds the git SHA of the code that judged the run.
  It comes from env `SENTINEL_VERSION`, which images bake in; host-native runs fall back to
  `git rev-parse --short HEAD`. A precision figure that mixes two builds describes neither.

**Verified by:** `tests/coded_tools/test_p1m.py` (fingerprint stability, id uniqueness, dimension
tagging, sample-floor refusal, unjudged handling, endpoint validation); `scripts/verify_p1m.py`
against the real DB with no LLM; and a browser check that a verdict and its note persist across a
reload and that the Calibration screen shows precision and missed bugs.

## 3. Phase 2 — Per-run sandbox

_Filled in when the phase closes._

## 4. Phase 3 — Packaging

_Filled in when the phase closes._

## 5. Phase 4 — Accounts, tenancy, BYOK

_Filled in when the phase closes._

## 6. Phase 5 — CI platforms

_Filled in when the phase closes._

## 7. Phase 6 — Test generation in CI

_Filled in when the phase closes._

## 8. Phase 7 — Remaining agreed items

_Filled in when the phase closes._

## 9. Phase 8 — Open-source readiness

_Filled in when the phase closes._
