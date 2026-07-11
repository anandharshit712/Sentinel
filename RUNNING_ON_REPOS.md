# Running Sentinel on any public repo

Two ways to point Sentinel at a real repository:

- **[Path A — Localhost / manual](#path-a--localhost--manual)** — fire a run yourself against any public git URL. No GitHub setup. Best for trying a repo quickly.
- **[Path B — Public URL / GitHub Action](#path-b--public-url--github-action)** — gate a repo's pull requests automatically. The repo's PRs post to your Gateway and the check fails unless Sentinel says `promote`.

Both use the **same** `POST /api/v1/simulate` endpoint: the Gateway clones the repo server-side, runs the Neuro-SAN review → test → risk → gating pipeline, and returns a decision.

---

## Prerequisites (once, on the host that runs Sentinel)

1. **Postgres 17** running; `DATABASE_URL` set in `.env`.
2. **`.env`** filled in (already present in this repo for dev): `NVIDIA_API_KEY`, `MODEL_NAME=mistralai/mistral-small-4-119b-2603`, `DATABASE_URL`, the `AGENT_*` vars.
   - Optional: `API_TOKENS="secrettoken:admin"` to require auth. If unset, the Gateway runs **OPEN_MODE** (every request is admin — dev only).
3. **`git`** on `PATH` (the Gateway clones repos).
4. **`python` + `pip` with outbound network** — needed only if you enable per-repo dependency install (see [Real tests](#making-a-repos-tests-actually-run)).
5. **`node` + `npm`/`npx` on `PATH`** — only needed for JS/TS repos (jest execution). Python-only repos don't need this.
5. **Bring the stack up** (DB migrate → build dashboard → Neuro-SAN `:8080` + Gateway `:8000`):

   ```powershell
   .\run.ps1
   ```

   Dashboard: <http://localhost:8000/> · stop with `.\run.ps1 -Stop`.

---

## Path A — Localhost / manual

Fire a run at the running Gateway for any public repo with the helper:

```powershell
$env:PYTHONPATH="."
.venv\Scripts\python.exe scripts\run_repo.py https://github.com/owner/repo
```

- No `--base`/`--head` → it uses the **last two commits** on the default branch (base=`HEAD~1`, head=`HEAD`). This reviews only the last commit.
- It prints a **watch URL** — open `http://localhost:8000/runs/<id>` to see the agent graph stream — then polls until the run finishes and prints `decision / risk / criticals`.

Options:

```powershell
# audit the WHOLE repo (no diff): diffs vs the git empty-tree, so every file reads as added.
# The security review adaptively fans out across 1-4 parallel reviewers sized to the repo.
.venv\Scripts\python.exe scripts\run_repo.py --full https://github.com/owner/repo

# a specific commit range (a PR's base..head)
.venv\Scripts\python.exe scripts\run_repo.py https://github.com/owner/repo --base <sha> --head <sha>

# choose the promotion transition (default dev -> test)
.venv\Scripts\python.exe scripts\run_repo.py https://github.com/owner/repo --to-env staging --from-env qa

# if the Gateway requires auth (API_TOKENS set)
.venv\Scripts\python.exe scripts\run_repo.py https://github.com/owner/repo --token secrettoken
```

**Audit mode (`--full`) semantics.** Use it to point Sentinel at any repo with zero CI wiring. The deliverable is the **review report + coverage** (secrets/dangerous-sink rules cover 100% of scanned lines; the LLM deep-reviews the highest-risk lines within its budget, spread across the fan-out). The **promote/escalate decision is advisory** here: the risk formula scores a *change*, and treating a whole repo as one change pegs churn factors high, so the band will skew toward `escalate` regardless of quality. Read the findings, not the verdict — the helper prints an `AUDIT MODE … decision is advisory` banner.

`repo.name` is derived as `owner/repo` from the URL so it matches [`config/repo_config.yaml`](config/repo_config.yaml) keys (timeouts, `install_deps`, sensitive rules).

> The Gateway clones the repo, so **both SHAs must be reachable on the default branch** of a full clone. Same-repo history only.

---

## Path B — Public URL / GitHub Action

Gate a repo's pull requests. The workflow builds a DeliveryEvent from the PR and posts it to your Gateway; the check passes only when the decision is `promote` (`escalate`/`hold` block the merge until reviewed in the dashboard).

### 1. Expose the Gateway publicly

GitHub's runners can't reach `localhost`. Tunnel port 8000:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Copy the printed `https://<something>.trycloudflare.com` URL. Keep the tunnel running. (Any tunnel works — ngrok, a real deploy, etc.)

### 2. Add the workflow to the target repo's default branch

Copy [`.github/workflows/sentinel-gate.yml`](.github/workflows/sentinel-gate.yml) into the target repo under `.github/workflows/` and commit it to **`main`** (pull-request workflows run from the base branch, so it must exist there).

### 3. Add two repo secrets

In the target repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `SENTINEL_GATEWAY_URL` | the tunnel URL from step 1 |
| `SENTINEL_TOKEN` | an admin token from your `API_TOKENS` (any value if the Gateway is in OPEN_MODE) |

### 4. Open a PR

Push a branch and open a PR (or push a commit to an existing PR). The **Sentinel gate** check runs, links the dashboard run in its job summary, and passes/fails on the decision.

Tune the promotion the gate represents in the workflow's `env:` block (`FROM_ENV` / `TO_ENV`, default `dev → test`).

**Limitations:** same-repo PRs only — the Gateway clones the base repo, so a fork's head SHA isn't reachable (fork PRs are skipped).

---

## Making a repo's tests actually run

By default Sentinel runs the pipeline with the **Gateway's own Python env**, so an external repo's tests can't import their dependencies — the review/quality/risk/decision stages still work, but the test stage reports no real results.

To run a repo's real tests, opt that repo into a **per-run virtualenv + dependency install** in [`config/repo_config.yaml`](config/repo_config.yaml):

```yaml
repos:
  owner/repo:              # must equal repo.name (owner/repo)
    <<: *defaults
    install_deps: true
    install_timeout_seconds: 900
```

Then `test_runner`:

- **auto-scans** the repo for Python project dirs at any depth (`pyproject.toml` / `requirements.txt` / `setup.py` / `setup.cfg` / `pytest.ini` / `tox.ini`), skipping `node_modules`, build/venv, and dot dirs — no need to list paths. Override with `test_project_dirs: [...]` only if auto-detection is wrong.
- creates a fresh venv per run, `pip install`s each project dir's deps (`-r requirements.txt` and/or `-e .`) plus `pytest`, and runs the selected tests with that venv.

> **Security:** `pip install -e .` runs the repo's build backend (arbitrary code) on the host. That's why `install_deps` is **opt-in per repo**, never global. Only enable it for repos you trust.

Caveats: each project dir must `pip install -e .` cleanly; installs must finish within `install_timeout_seconds`; tests that need live services (a database, redis, etc.) will error in the isolated venv and be reported as a `stage_failure` — the gate still works from review/risk.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `event missing ...` (400) | the event needs `repo.url`, both SHAs, and a `target_transition`. The helper fills these in. |
| run goes to `failed` fast | clone failed — check the repo URL is public and both SHAs exist on the default branch. |
| test stage shows 0 tests / `none_detected` | no Python markers found, or deps not installed — enable `install_deps` (above). |
| Action can't reach the Gateway | tunnel down, or `SENTINEL_GATEWAY_URL` wrong — confirm the tunnel URL responds at `/healthz`. |
| `401 invalid or missing token` | `API_TOKENS` is set — pass a matching admin token (`--token`, or the `SENTINEL_TOKEN` secret). |
