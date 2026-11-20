# 12 — Platform plan: measured reviews, sandbox, packaging, tenancy, CI, test generation in CI

Status: built (2026-11-20). The gates that need a Docker engine, Keycloak, or a user's own OAuth apps
and GitLab project are listed in `PROGRESS.md` §7b as partial, each with the reason. Documents 01–07 describe the hackathon build and stay frozen;
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

**Problem.** Seventeen call sites ran repository code with a plain `subprocess.run` on the host:
installs, tests, mutation runs, oracle probes. The blind-oracle probe even ran with the host's
unscrubbed environment, NVIDIA key included. That is acceptable for a demo on sample repos, and
not for strangers' code.

**Design.**

- **One choke point, `lib/sandbox.py`.** `run(run_id, argv, cwd, timeout, network, env)`:
  - `SANDBOX_MODE=local` (default) is a plain subprocess, as before. It is used by host-native dev
    and the unit tests.
  - `SANDBOX_MODE=docker` sends the call to sandboxd.
  - Read-only `git` and tree-sitter parsing stay in-process, because they never execute repo code.
  - The environment is scrubbed of anything credential-shaped in both modes, and scrubbed again by
    sandboxd, which is the trust boundary.
- **`sandboxd/server.py`.** A stdlib-only HTTP service and the only component allowed to talk to
  Docker (`SANDBOXD_TOKEN` bearer, binds 127.0.0.1). The Gateway calls
  `sandbox.open_run(run_id, workspace, repo)` after the clone and `close_run(run_id)` in the run's
  `finally`. Coded tools exec by `run_id`. In docker mode, an unknown run is an error: nothing ever
  falls back to the host.
- **Per-run container.**
  - Mounts: only that run's workspace at `/work`, plus a sibling scratch dir at `/scratch`. The
    scratch dir lives outside the repo so a copied repo does not pick up the original's
    config files.
  - No host environment and no secrets.
  - Hardening: `--cap-drop ALL`, `no-new-privileges`, read-only root with a `/tmp` tmpfs.
  - Limits: CPU and memory from `runner_quota` in `config/repo_config.yaml`, swap off, and a pids
    limit against fork bombs.
  - Timeouts are enforced with `timeout -k` *inside* the container, because killing `docker exec`
    leaves the process running.
  - The network is switched per exec: connected for the install phase, disconnected for tests,
    mutation and oracle. Execs within one run are serialised.
  - The container is removed with its scratch files at `close_run`. On restart, sandboxd removes
    containers it no longer tracks.
- **Per-repo dependency cache volume** (pip/npm), never shared between repos. *Accepted risk:* one
  PR's install phase can poison the cache for later runs of the same repo.
- **Image** `sandbox/Dockerfile`: Python 3.12 + Node 20 + git + pytest/pytest-cov, multi-arch.
  Runtime via `SANDBOX_RUNTIME`: `runc` in dev, `runsc` (gVisor) for hosted.
- **Clone hardening:** `core.symlinks=false`. A repo symlink is checked out as a plain file, so the
  analysis tools, which read the tree on the host, cannot be pointed at another run's workspace or
  at a host file.
- `ponytail:` install-phase egress is open, since there is nothing secret inside the container to
  steal. Add an allow-list proxy to package registries if hosted abuse shows up. Egress can reach
  `host.docker.internal`, so the host's Postgres must never use trust auth.
- In docker mode the image's interpreter has only pytest/pytest-cov. A repo that relied on the
  Gateway venv's packages needs `install_deps: true`, which is now safe because installs run
  inside the sandbox.

**Verified by:** `tests/coded_tools/test_sandbox.py` covers both modes. Docker mode runs the real
sandboxd HTTP server with the docker CLI faked. Still to verify against a real Docker engine: the
escape suite `scripts/verify_sandbox.py` (fixture `samples/sandbox-escape/`, which tries to read
env secrets, reach the network during tests, read another run's workspace and fork-bomb; each test
passes only if its escape failed), and `verify_b4`/`verify_p3` in docker mode.

## 4. Phase 3 — Packaging

- **One application image**, `deploy/app.Dockerfile`. The SPA is built in a Node stage and served
  by the Gateway from one origin. The image runs as uid 10001, and the same image runs both the
  Gateway (default command, which applies migrations first) and the neuro-san server. It never runs
  repository code, because `SANDBOX_MODE=docker` hands that to sandboxd. `SENTINEL_VERSION` is a
  build arg, baked into the image and stamped on every run.
- **`deploy/sandboxd.Dockerfile`**: alpine + Python + the docker CLI. sandboxd is the only service
  with the Docker socket; it is not published on any host port and requires `SANDBOXD_TOKEN`.
- **`deploy/compose.yaml`.**
  - Services with no profile form **core**: postgres 17, `sandbox-image` (builds the per-run image,
    then exits), sandboxd, neuro-san and gateway (:8000).
  - `--profile hosted` adds Keycloak 26 (:8180), which imports `deploy/keycloak/realm-sentinel.json`
    and uses its own role and database on the same Postgres.
  - Named volumes `pgdata` and `sentinel_workspaces`. Healthchecks gate the start order.
- **Postgres init** (`deploy/postgres/init-sentinel.sh`) creates the application role as
  `NOSUPERUSER NOBYPASSRLS`. A superuser would silently bypass row-level security, so the default
  `POSTGRES_USER` must never be the application's role.
- **Workspaces in compose.** The Gateway clones into the shared volume. sandboxd itself runs in a
  container, so a bind path would mean nothing to the Docker daemon. Instead it mounts only the
  run's own two subdirectories, using `--mount type=volume,…,volume-subpath=<run_id>`
  (Docker Engine 26+).
- **`.env.example`** documents every variable for both host-native and compose. Compose reads it
  with `--env-file .env`, and refuses to start without `POSTGRES_PASSWORD`, `SENTINEL_DB_PASSWORD`
  and `SANDBOXD_TOKEN`.
- `run.ps1` (host-native dev) is unchanged.
- **For the later deploy discussion:** a GCP free e2-micro (1 GB) cannot run this stack; an Oracle
  A1 (4 OCPU / 24 GB, arm64) can. Every base image is multi-arch.

## 5. Phase 4 — Accounts, tenancy, BYOK

One codebase, two auth modes:

- `AUTH_MODE=tokens` (default). Today's shared-token shim. A self-hosted install is one tenant
  (the `legacy` account) and its operator.
- `AUTH_MODE=oidc`. Hosted mode, described below.

**Identity is Keycloak** (`deploy/keycloak/realm-sentinel.json`). Keycloak owns everything
security-sensitive about login: email+password with verification, brute-force lockout, optional
TOTP, a 12-character password policy, and GitHub/GitLab sign-in. Sentinel writes no login
security of its own.

- **Sign-in.** The Gateway runs the OAuth code flow as a confidential client (BFF): `/auth/login`
  → Keycloak → `/auth/callback`. Sessions live in HttpOnly cookies, and the SPA never holds a
  token. An expired access token is renewed from the refresh cookie on the next request.
  `gateway/auth.py` verifies tokens against the realm JWKS and accepts only tokens issued to the
  Sentinel client (`azp`).
- **Link gate.** Until a GitHub or GitLab account is linked, every route except profile and linking
  answers 403 `link_required`, and the SPA shows only the linking page. Linking uses Keycloak's
  client-initiated account linking. The IdPs store their tokens (`storeToken`), and the Gateway
  reads them through the broker token endpoint (`read-token` is a realm default role). One
  code-host account belongs to at most one Sentinel account (`UNIQUE (provider, subject)`).
  Unlinking removes the federated identity in Keycloak, and in the same transaction suspends every
  repo registered through that host and revokes its CI keys. A link found missing at the next
  `/whoami` is treated the same way.
- **Repo ownership.** GitHub needs `permissions.admin`; GitLab needs access level >= Maintainer.
  Both are asked of the host with the linked user's own token. Any other host is registered
  `pending` with a one-time token and stays pending until `.sentinel-verify` holds it on the
  default branch. Repos are keyed by the host's immutable id, so a rename cannot hijack a
  registration. Token mode skips ownership: the admin-token holder is the operator.
- **CI auth.**
  - GitHub Actions and GitLab CI send their OIDC job token. The Gateway verifies issuer, audience
    (`CI_OIDC_AUDIENCE`), signature and expiry, then matches the signed repository id to the
    registration. No long-lived secret is involved.
  - Jenkins sends a per-repo API key (`X-Sentinel-Key`), shown once and stored as a sha256 hash.
  - `POST /api/v1/ci/runs` additionally requires the event's clone URL to be the registered repo's.
- **Tenancy in the database.**
  - Migration 0006 adds `accounts`, `identities`, `repos`, `repo_keys` and `llm_keys`, plus
    `account_id` on `runs`, `audit_events` and `incidents`. Existing rows are backfilled to
    `legacy`.
  - Migration 0007 enables `FORCE ROW LEVEL SECURITY` on every tenant table. Every transaction
    sets `app.account_id`: an account uuid, `*` for system work, or `''`, which sees nothing.
  - In hosted mode every request starts in `''`, and only authentication widens it. A handler that
    forgets to authenticate fails closed.
  - A trigger fills `account_id` from the run or from the scope, so no insert site can mislabel a
    row.
  - The neuro-san coded tools run as system scope. They only write under the `run_id` the Gateway
    put in `sly_data`, which the LLM cannot see or change.
- **Bring your own key (BYOK).**
  - Keys are validated on save with one real tool-call request against the primary model.
  - Keys are encrypted with Fernet under `SENTINEL_SECRET_KEY` (`lib/secretbox.py`). Only a
    4-character hint is ever shown.
  - With `LLM_KEY_SOURCE=sly_data`, every NIM model config reads `nvidia_api_key` from the
    request's `sly_data.llm_config`: neuro-san's `replace_any_required_api_keys` swaps the literal
    `"sly_data"` for that value.
  - The Gateway hands each run its repo's key, else its account's key, never the host's.
    `scripts/verify_byok.py` proved, live on 2026-11-20: no key in `sly_data` → the run is refused
    even with the server's env key present; with the key → the run promotes; the key appears in
    neither the returned `sly_data`, the server log, nor the thinking files.
- **Abuse controls (hosted).** Runs per 24 h per account (`QUOTA_RUNS_PER_DAY_ACCOUNT`, default 50)
  and per repo (default 20), and a maximum repo size (`MAX_REPO_MB`, default 500). Platform admins
  (`SENTINEL_ADMIN_EMAILS`) can suspend or reactivate accounts, and the account's status is checked
  on every request. `audit_events.account_id` is the usage log.
- **Privacy.** A privacy page states what leaves the box: code snippets go to the LLM provider
  under the user's own key. Runs and findings past `accounts.retention_days` (default 90) are
  purged daily, in hosted mode only. A self-hosted install's history is its operator's to keep.

**Verified by:** `tests/gateway/test_tenancy.py`, which covers:

- CI tokens: wrong audience, issuer, signing key or expiry are rejected.
- A token minted for another client is not a session.
- The link hash, the link gate, quota and suspension.
- Against the real database: A cannot read B's run or child rows by direct id, cannot write into
  B's run, an unscoped transaction sees nothing, and audit rows follow their run.
- The API key is stored hashed and dies on revoke.
- Unlinking suspends repos and revokes keys.
- A host account links to only one Sentinel account.

Also `verify_byok.py` (live), and `scripts/verify_tenancy.py`: two real Keycloak users signed in
through the Gateway, isolated over HTTP.

## 6. Phase 5 — CI platforms

All three templates post to `POST /api/v1/ci/runs` and poll `GET /api/v1/ci/runs/{id}`. Neither
endpoint takes a user session: the credential identifies exactly one registered repo, and the
event's clone URL has to be that repo's.

| Platform | Credential | Comment back |
|---|---|---|
| GitHub Actions (`.github/workflows/sentinel-gate.yml`) | GitHub OIDC job token, re-minted for every call because it lives only minutes; `SENTINEL_API_KEY` secret as a fallback | One PR comment, edited in place (`gh pr comment --edit-last`) |
| GitLab CI (`ci-templates/gitlab/sentinel.gitlab-ci.yml`) | `id_tokens` with `aud: sentinel`; `SENTINEL_API_KEY` as a fallback | One MR note, found by a marker and updated, if `SENTINEL_GITLAB_TOKEN` is set |
| Jenkins (`ci-templates/jenkins/Jenkinsfile`) | Per-repo API key from the `sentinel-api-key` credential (Jenkins has no OIDC identity Sentinel can verify) | Console summary; a GitHub PR comment if `sentinel-github-token` exists |

- The GitHub job is skipped when the `SENTINEL_GATEWAY_URL` variable is unset, so the template
  cannot fail on repos that have not opted in. It is also skipped for fork PRs.
- Self-hosted Sentinel registers a public GitHub/GitLab repo with its immutable host id, fetched
  anonymously, so OIDC works without hosted mode.
- The README warns about self-hosted runners on public repos.

**Live proof (GitHub, 2026-11-20)** on `anandharshit712/ORION` PR #12, through an ngrok tunnel to a
host-native Gateway:

- The OIDC job token was accepted (`202`), the run completed, and the PR comment was posted. The
  check failed on the gate's decision (`escalate`), which is the intended behaviour.
- The run itself exposed two scoring problems, logged rather than fixed here because each changes
  the gate:
  - Compliance-dimension highs are weighted as `security.high`.
  - Uninstalled test dependencies are scored as test failures.

## 7. Phase 6 — Test generation in CI

**The rule** (`lib/untested.py`, deterministic). A changed function or method in a non-test `.py`
or JS/TS file becomes a generation candidate only if all three are false:

- (a) any body line ran under coverage (partial coverage counts as covered: extend the test that
  already reaches it);
- (c) a test file changed in this PR references the name;
- (b) an existing test imports its module (Python dotted-suffix match, JS relative-import
  resolution) and uses the name as an identifier, found with `ast` or tree-sitter. Comments and
  strings do not count.

Every skipped function records why. Candidates are ranked sensitive → new → size and capped by
`max_generated_per_pr` (default 3); `truncated` says how many were cut. *Unmeasured* counts as
never executed: Jest instruments only the files the tests load, so a brand-new untested module is
simply absent from lcov. No candidates → no model call and nothing posted.

**When it runs.** For CI-sourced runs only, the Gateway runs `sentinel_testgen` *after* the decision
is persisted and published. It is a background task on a fresh clone, in the run's own sandbox,
with the run's own BYOK key, so it can never delay or alter the gate. Generation is one network
call per candidate, with one retry. `GET /api/v1/ci/runs/{id}` reports `test_generation`
(`pending` / `running` / `done` / `none`) and the proposals.

The GitHub and GitLab templates have a second, advisory step (`continue-on-error` /
`allow_failure`). It waits for generation and posts the accepted tests as their own comment or
note. Each comment is updated in place, found by its marker, so the gate comment and the tests
comment never overwrite each other. Nothing is ever committed.

**JS/TS path.**

- Jest runs with `--coverage --coverageReporters=lcovonly`. `gap_context` is language-aware:
  module path, framework, `*.test.*` examples.
- `test_author` writes pytest or Jest accordingly.
- `lib/evaluate` JS mode:
  1. Reject a test with no assertions.
  2. Require a pass on the unmodified code.
  3. Run **Stryker 10.1.1** (`@stryker-mutator/core` + `jest-runner`) with `mutate: file:start-end`
     and `testMatch` limited to the proposed test.
  4. Score = (Killed + Timeout) / (Killed + Timeout + Survived + NoCoverage). CompileError and
     RuntimeError mutants are excluded.
- Stryker is installed with `npm install --no-save --ignore-scripts` in the sandbox install phase
  (network on), so package.json and the lockfile are untouched and no lifecycle script runs. It
  needs Node ≥ 22, so the sandbox image moved to Node 22.
- The differential tier gained a Node runner. The blind oracle uses JSDoc as the stated intent and
  removes the body. `spec_check` stays Python-only and says `checked: false`.
- Measured on the sample: about 28 s to install, 11–15 s per Stryker evaluation.

**Verified by:**

- `tests/coded_tools/test_p6.py` (34 tests): all three conditions in both languages, the
  PR-added-test case, the cap, zero calls with no candidates, and the JS plumbing.
- `verify_p6_js.py` 4/4 on `samples/node-catalog-service`: the proposal was accepted with a Stryker
  score of 1.00 (11/11 killed), the repo's git status stayed clean, and a PR whose changed
  functions are all covered → zero candidates, zero model calls.
- `verify_p3` 3/3, every trial 1.00.
- A Gateway test: CI runs start generation after `done`; manual runs never do.

## 8. Phase 7 — Remaining agreed items

**Licence resolution** (`lib/license_rules.py`, `license_scanner`).

- **What is looked up.** Dependencies the diff adds in `requirements*.txt`, `package.json` and
  `pyproject.toml` (PEP 621 dependencies, optional-dependencies and dependency-groups). Poetry
  tables are not read.
- **Where.** PyPI `/pypi/<name>/json` and the npm registry `/<name>/latest`. Both use the latest
  release; the manifest's version range is not solved. PyPI trove classifiers are read before the
  free-text `license` field, because some packages paste the whole licence text into that field.
- **Limits.** stdlib `urllib`, 5 s timeout, at most 30 lookups per call. The first unreachable
  registry stops further lookups, so being offline costs one timeout, not thirty.
- **Cache.** `.cache/licenses/`, 7-day TTL, successful lookups only (gitignored).
  `SENTINEL_LICENSE_LOOKUP=off` reads the cache only, for air-gapped installs and the test suite.
- **Unresolved.** A package whose licence could not be resolved is reported per package with its
  reason, and the note says it is "not checked, NOT clean". It never counts as permissive.
- **Findings.** One finding per licence family per call:
  - copyleft (GPL/AGPL/SSPL/EUPL/CC-BY-SA): `medium`
  - weak copyleft (LGPL/MPL/EPL): `low`

  Licence findings alone add about 7 risk points at most, which stays inside the low band, so a
  licence can never fail the gate on its own.
- **Live check.** pyqt5 resolved as GPL v3 → one medium finding. requests, react and
  `@types/node` resolved as permissive. A nonexistent package came back unresolved (HTTP 404).

**`spec_check` invariants** (`lib/spec_check.py`). All three are static: each compares something
the author wrote (a docstring or annotation) with literal values on the code's paths. Like the
existing checks, they never say which side is wrong.

- **Bounds:** the docstring promises a range or "non-negative", and a literal return falls outside
  it.
- **Idempotence:** the docstring promises idempotence, and the function unconditionally appends to
  or increments state that outlives the call.
- **Type preservation:** a literal return contradicts the return annotation (int-for-float allowed;
  a `None` return is allowed when the docstring mentions None).

The agreed gate was zero false positives on the `humanize` trial set (`scripts/spec_check_fp.py`,
humanize `785e5dc`, 9 trial functions / all 37 in `src`):

| Invariant | Judged | Fired | Kept |
|---|---|---|---|
| bounds | 0 / 0 | 0 / 0 | yes |
| idempotence | 0 / 0 | 0 / 0 | yes |
| type preservation | 9 / 29 | 0 / 0 | yes |

Caveats, stated with the numbers:

- Bounds and idempotence pass only because no humanize docstring makes those claims.
- Type preservation was tuned on the trial set: before the "None mentioned in the docstring" rule,
  it fired once, on `clamp`.
- Outside the trial set, over the Python 3.12 stdlib and its site-packages (300,355 functions):
  - bounds judged 21 and fired 0
  - idempotence judged 21 and fired 0
  - type preservation judged 42,458 and fired 13; all 13 are real annotation-versus-code
    disagreements, for example urllib3's `-> bytes` returning `None`.

**Proposals 9–14** were relabelled `characterization` by `scripts/mark_unverified_proposals.py`.
The script is idempotent, and the old label is kept under `evaluation.reclassified_from`. A
`pg_dump` of the schema was taken first.

## 9. Phase 8 — Open-source readiness

- **README.**
  - "Self-host with Docker": clone → `cp .env.example .env` → `docker compose … up`, plus a
    `verify_b4` check run inside the Gateway container.
  - Hosted-mode setup (OAuth app callbacks, which secrets).
  - "What runs where, and what leaves the box": the sandbox, LLM provider traffic under whose key,
    licence lookups, row-level security, CI credentials.
  - "CI integration" per platform, with the self-hosted-runner warning.
  - Repository layout covering `deploy/`, `sandbox/`, `sandboxd/` and `ci-templates/`.
- **`.env.example`** documents every variable.
- **The gate** (fresh clone → README only → working stack, `verify_b4` PASS) needs a Docker engine,
  which was not available on 2026-11-20.
