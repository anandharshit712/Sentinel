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

_Filled in when the phase closes._

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

_Filled in when the phase closes._

## 7. Phase 6 — Test generation in CI

_Filled in when the phase closes._

## 8. Phase 7 — Remaining agreed items

_Filled in when the phase closes._

## 9. Phase 8 — Open-source readiness

_Filled in when the phase closes._
