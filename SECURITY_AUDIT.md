# Sentinel — Security Audit

**Date:** 2026-07-10
**Scope:** Full repository (`gateway/`, `scripts/`, `coded_tools/sentinel/`, `db/`, `frontend/`, `registries/`, `config/`, `.github/`, `.env`/logging). The vendored `neuro-san-studio/` clone is a read-only reference and was audited only where Sentinel imports from it.
**Method:** Manual code review tracing every request/data path across trust boundaries, plus `bandit`, `pip-audit` (direct + full installed environment), `npm audit`, a git-history secrets scan, and a working proof-of-concept for the path-traversal finding.
**Threat model:** Adversarial. Every input crossing a trust boundary is treated as hostile — HTTP callers, and critically **the contents of the arbitrary repositories this tool clones and executes** (file paths, diff text, test IDs, dependency manifests, `base`/`head` SHAs).

---

## Executive summary

Sentinel's static-analysis, contract, and database layers are genuinely well built: SQL is fully parameterized, there is no `shell=True` anywhere, deserialization is safe (`yaml.safe_load`/`json.load`, no `pickle`/`eval`), the frontend has zero XSS sinks, secrets are kept out of git history, and the "code decides, LLM only raises risk" invariant is enforced in code. **However, the product's central feature — "run Sentinel on any public repo" — is a remote-code-execution engine with no containment, fronted by authentication that is trivially bypassed in the shipped configuration.** Four issues are Critical: an _unauthenticated_ path-traversal that serves the `.env` (leaking the NVIDIA key, all API tokens, and DB credentials — proven live); arbitrary `git clone` targets that permit `ext::`-transport RCE and SSRF; unsandboxed execution of the cloned repo's own test code on every run; and authentication that defaults to "everyone is admin" (or, as actually configured in `.env`, uses the exact demo tokens documented in the public source). These chain: one unauthenticated HTTP request reads the admin token from `.env`, and the admin token turns the clone/test pipeline into code execution on the Gateway host. This is a pre-launch blocker set, but every Critical has a small, well-scoped fix except the sandboxing work (C3), which is the one item that needs real engineering before the tool is pointed at untrusted repositories.

---

## Findings

| #   | Severity     | CWE                | Location                                                                                                                                                                                                                                | Summary                                                                                                                                                   |
| --- | ------------ | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | **Critical** | CWE-22             | [gateway/app.py:390-395](gateway/app.py#L390-L395)                                                                                                                                                                                      | Unauthenticated SPA catch-all serves any file via `..` traversal — leaks `.env` (NVIDIA key, API tokens, DB creds). **Proven.**                           |
| C2  | **Critical** | CWE-78 / CWE-918   | [gateway/app.py:129](gateway/app.py#L129)                                                                                                                                                                                               | Attacker-controlled `repo.url` → `git clone` with no scheme allow-list or `--`: `ext::` transport RCE, `file://` local read, SSRF to internal hosts.      |
| C3  | **Critical** | CWE-94 / CWE-829   | [coded_tools/sentinel/test_runner_tool.py:118](coded_tools/sentinel/test_runner_tool.py#L118), [:255](coded_tools/sentinel/test_runner_tool.py#L255), [:315](coded_tools/sentinel/test_runner_tool.py#L315)                             | Cloned repo's `conftest.py`/test code executes on the Gateway host with no sandbox — on every run, even without `install_deps`.                           |
| C4  | **Critical** | CWE-798 / CWE-1188 | [gateway/settings.py:38](gateway/settings.py#L38), [.env:22](.env#L22)                                                                                                                                                                  | Auth fails open to admin when `API_TOKENS` is unset; the shipped `.env` uses the exact demo tokens documented in [settings.py:4](gateway/settings.py#L4). |
| H1  | **High**     | CWE-88             | [git_diff_tool.py:135-136](coded_tools/sentinel/git_diff_tool.py#L135-L136), [ast_analyzer_tool.py:24](coded_tools/sentinel/ast_analyzer_tool.py#L24), [dependency_graph_tool.py:73](coded_tools/sentinel/dependency_graph_tool.py#L73) | Unvalidated `base`/`head` SHAs passed to git with no `--` → `git diff --output=<path>` arbitrary file write.                                              |
| H2  | **High**     | CWE-312 / CWE-522  | [db/dao.py:32-37](db/dao.py#L32-L37), [sentinel-gate.yml:64](.github/workflows/sentinel-gate.yml#L64)                                                                                                                                   | Clone URL with embedded git token stored cleartext in `runs.event` JSONB and returned to any viewer via `GET /runs/{id}`.                                 |
| M1  | Medium       | CWE-1395           | [requirements.txt](requirements.txt) (resolved env)                                                                                                                                                                                     | Installed environment has known-vulnerable transitive deps: `transformers 4.43.3`, `urllib3 2.3.0`, `werkzeug 3.1.3`, `wheel 0.45.1`.                     |
| M2  | Medium       | CWE-88             | [test_runner_tool.py:255](coded_tools/sentinel/test_runner_tool.py#L255), [:315](coded_tools/sentinel/test_runner_tool.py#L315)                                                                                                         | Repo-derived test IDs spliced into pytest/jest argv with no `--` separator (option injection).                                                            |
| M3  | Medium       | CWE-209 / CWE-532  | [gateway/app.py:192-193](gateway/app.py#L192-L193)                                                                                                                                                                                      | Raw exception text (may carry DSN / tokenized clone URL) persisted to audit log and streamed over SSE, bypassing `redact.py`.                             |
| M4  | Medium       | CWE-22             | [test_mapper_tool.py:135](coded_tools/sentinel/test_mapper_tool.py#L135)                                                                                                                                                                | `added_test_ids` existence check doesn't confine to workspace → pytest imports files outside the clone.                                                   |
| M5  | Medium       | CWE-250            | [db/dao.py:28](db/dao.py#L28)                                                                                                                                                                                                           | App runs HTTP traffic as the schema-owner DB role (can `DROP`/`ALTER`); no least-privilege app role provisioned.                                          |
| M6  | Medium       | CWE-22             | [lib/workspace.py:17](lib/workspace.py#L17)                                                                                                                                                                                             | Traversal guard regex allows a bare `..` component (latent; `run_id` is a UUID today).                                                                    |
| M7  | Medium       | CWE-400            | [git_diff_tool.py:27](coded_tools/sentinel/git_diff_tool.py#L27), [test_mapper_tool.py:94-95](coded_tools/sentinel/test_mapper_tool.py#L94-L95)                                                                                         | git subprocess calls have no `timeout` and no output cap; test files read whole → OOM from a hostile repo.                                                |
| L1  | Low          | CWE-522            | [frontend/src/lib.tsx:7](frontend/src/lib.tsx#L7)                                                                                                                                                                                       | Auth token stored in `sessionStorage` + sent as Bearer (readable by any origin JS if an XSS is ever introduced).                                          |
| L2  | Low          | CWE-636            | [frontend/src/lib.tsx:46](frontend/src/lib.tsx#L46)                                                                                                                                                                                     | Client auth gate fails **open** (`openMode=true`) if `/healthz` is unreachable (cosmetic; server still enforces).                                         |
| L3  | Low          | CWE-208            | [gateway/app.py:101](gateway/app.py#L101)                                                                                                                                                                                               | Token compared by dict lookup, not constant-time → timing side-channel on token value.                                                                    |
| L4  | Low          | CWE-352            | [frontend/src/lib.tsx:62](frontend/src/lib.tsx#L62)                                                                                                                                                                                     | `POST /logout` authenticated by ambient cookie only (CSRF logout nuisance).                                                                               |
| L5  | Low          | CWE-614            | [gateway/app.py:259-260](gateway/app.py#L259-L260)                                                                                                                                                                                      | Session cookie `Secure` flag derived from `request.url.scheme` → not set behind a TLS-terminating proxy.                                                  |
| L6  | Low          | CWE-918            | [notification_tool.py:42](coded_tools/sentinel/notification_tool.py#L42)                                                                                                                                                                | `urlopen` on operator-set `NOTIFY_WEBHOOK_URL` with no scheme restriction (`file://` allowed).                                                            |
| L7  | Low          | CWE-611            | [test_runner_tool.py:132](coded_tools/sentinel/test_runner_tool.py#L132)                                                                                                                                                                | JUnit XML from repo-controlled pytest parsed with stdlib `ElementTree` (use `defusedxml`).                                                                |
| L8  | Low          | CWE-59             | [test_mapper_tool.py:94](coded_tools/sentinel/test_mapper_tool.py#L94)                                                                                                                                                                  | Test-file reads follow symlinks → a symlinked `test_*.py` can point at `.env`.                                                                            |
| L9  | Low          | CWE-20             | [frontend/src/sse.ts:20](frontend/src/sse.ts#L20)                                                                                                                                                                                       | Unhandled `JSON.parse` on SSE frames → one bad frame stalls the live view.                                                                                |
| I1  | Info         | CWE-916            | [samples/.../auth/login.py:13](samples/python-payments-service/app/auth/login.py#L13)                                                                                                                                                   | Demo fixture hashes passwords with unsalted SHA-256 (sample only, not a real code path).                                                                  |
| I2  | Info         | —                  | [db/migrations/versions/0001_baseline.py:53](db/migrations/versions/0001_baseline.py#L53)                                                                                                                                               | Security enums (`band`, `decision`, `status`) are bare `TEXT` — no DB `CHECK` backstop.                                                                   |

---

## Critical findings — detail

### C1 — Unauthenticated path traversal serves arbitrary files (leaks all secrets)

**Location:** [gateway/app.py:390-395](gateway/app.py#L390-L395)

```python
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> FileResponse:
    if full_path.startswith(("api/", "healthz")):
        raise HTTPException(404)
    f = _DIST / full_path
    return FileResponse(f if f.is_file() else _DIST / "index.html")
```

The SPA fallback joins a user-controlled URL path to `_DIST` and serves the result with **no containment check and no authentication dependency** (unlike every `/api/*` route, this handler has no `Depends(_require(...))`). Unlike the `/assets` mount, it does not use Starlette's `StaticFiles` (which has traversal protection) — it builds `FileResponse` by hand.

**Exploit (proven):** Issued directly against the ASGI app with a raw path (browsers normalize `..`, but `curl --path-as-is`, URL-encoded `%2e%2e`, and most proxies forward it):

```
GET /../../.env            -> 200, serves C:\Users\anand\Downloads\Sentinel\.env  (1085 bytes)
GET /../../requirements.txt -> 200, serves the real requirements.txt
```

The `.env` payload contains `NVIDIA_API_KEY`, `DATABASE_URL` (with credentials), and `API_TOKENS` — i.e. the **admin token**. This single unauthenticated request hands an attacker the credential needed for C2/C3. This is the top-priority fix: it is unauthenticated, trivially exploitable, and is the entry point of the full compromise chain.

**Remediation:** Serve the SPA with `StaticFiles(directory=_DIST, html=True)` instead of a hand-built `FileResponse`, **or** resolve and confine the path:

```python
f = (_DIST / full_path).resolve()
if not str(f).startswith(str(_DIST.resolve()) + os.sep) or not f.is_file():
    return FileResponse(_DIST / "index.html")
return FileResponse(f)
```

Rotate the NVIDIA key, the DB password, and all API tokens after remediation (assume they are exposed).

### C2 — Arbitrary `git clone` target → RCE, SSRF, and local file disclosure

**Location:** [gateway/app.py:120-133](gateway/app.py#L120-L133) (`_clone`), reached by `POST /api/v1/simulate`

```python
url = ((event.get("repo") or {}).get("url") or "").strip()
...
r = subprocess.run(["git", "-c", "core.longpaths=true", "clone", "--quiet", url, ws], ...)
```

`event.repo.url` is fully attacker-controlled and only presence-checked ([`_validate_event`, app.py:219-225](gateway/app.py#L219-L225) never validates the scheme). There is no `--` separator before `url` and no protocol allow-list.

**Exploit vectors:**

- **RCE:** `repo.url = "ext::sh -c touch${IFS}/tmp/pwned"`. Git's `ext::` transport is allowed by default for direct clones and executes an arbitrary command as the Gateway user.
- **Argument injection:** a `url` beginning with `-` (e.g. `--upload-pack=<cmd>`) is parsed by git as an option because it sits in positional slot with no `--` guard.
- **SSRF:** `repo.url = "http://169.254.169.254/…"` or an internal git host reaches internal-only services; because the clone's contents are then surfaced in the diff/review report, a reachable internal git repo can be **exfiltrated**.
- **Local disclosure:** `repo.url = "file:///c:/…"` clones a local repository into the workspace.

**Remediation:** Add `"--"` before `url`; allow-list schemes to `https://` only (reject `ext::`, `file://`, `ssh://`, and anything starting with `-`); set `GIT_ALLOW_PROTOCOL=https` in the clone subprocess env; strip credentials from the URL before logging/persisting (see H2). Consider resolving the host and blocking RFC-1918 / link-local ranges to close the SSRF angle.

### C3 — Unsandboxed execution of untrusted repo code

**Location:** [test_runner_tool.py](coded_tools/sentinel/test_runner_tool.py) — collection [:118](coded_tools/sentinel/test_runner_tool.py#L118), run [:255](coded_tools/sentinel/test_runner_tool.py#L255)/[:315](coded_tools/sentinel/test_runner_tool.py#L315), dependency install [:101-111](coded_tools/sentinel/test_runner_tool.py#L101-L111)/[:162-163](coded_tools/sentinel/test_runner_tool.py#L162-L163)

Running (or merely _collecting_) pytest imports the repo's `conftest.py` and test modules — arbitrary Python — and jest loads `jest.config.js` as Node. **This fires on the default path of every `simulate` run**, using the Gateway's own interpreter; it does not require `install_deps`. When `install_deps` is enabled, `pip install -e .` / `pip install -r requirements.txt` / `npm install` additionally run the repo's build backend, arbitrary `--index-url`, and `postinstall` scripts.

The existing mitigations — a secret-scrubbed environment ([`_scrubbed_env`, :43](coded_tools/sentinel/test_runner_tool.py#L43)), a timeout, and gating `install_deps` behind a per-repo allow-list — are real and worth keeping, **but none of them provide isolation**. The executed code runs as the Gateway user with full filesystem access (it can read `.env` directly, regardless of env scrubbing), full network egress, and write access to the source tree.

**Exploit:** any hostile repo ships `conftest.py` containing `import os, socket; ...`. It runs the instant `test_runner` touches the repo.

**Remediation:** Execute the entire clone/collect/install/test step inside a locked-down sandbox: a container run as a non-root user, `--network none` (or an egress allow-list), a read-only bind of the workspace, dropped capabilities, and memory/pids/CPU limits. Treat "we run the repo's tests" as inherently code-execution and contain it — env scrubbing is necessary but not sufficient. Until this exists, restrict `simulate` to a vetted repo allow-list.

### C4 — Authentication trivially bypassed (fail-open default + documented tokens)

**Location:** [gateway/settings.py:24-38](gateway/settings.py#L24-L38), [.env:22](.env#L22)

```python
API_TOKENS = _parse_tokens()
OPEN_MODE = not API_TOKENS  # no tokens configured -> everyone is admin (dev)
```

Two failure modes:

1. **Fail-open:** if `API_TOKENS` is unset, `_role_for` returns `admin` for _every_ request ([app.py:97-102](gateway/app.py#L97-L102)) — `simulate` (admin-only) is wide open.
2. **Documented credentials:** the shipped `.env` sets `API_TOKENS=admintok:admin,apprtok:approver,viewtok:viewer` — the _exact_ example values written in the [settings.py docstring](gateway/settings.py#L4), which is public source. Anyone who reads the repo knows the admin token is `admintok`.

Either way an attacker reaches admin, and admin → C2/C3 → RCE. (The token is also readable unauthenticated via C1.)

**Remediation:** Fail **closed** — if no tokens are configured, refuse privileged routes (or bind to `127.0.0.1` and log a loud warning) rather than granting admin. Generate real high-entropy tokens (`secrets.token_urlsafe(32)`) for any non-localhost deployment; never ship the documented demo values. Move to OIDC/mTLS for production as already planned. Compare tokens with `hmac.compare_digest` (see L3).

---

## High findings — detail

### H1 — git argument injection via unvalidated base/head SHAs → arbitrary file write

`base`/`head` flow unvalidated from `event.change.*` through [`workspace.run_inputs`](lib/workspace.py#L43-L62) into git commands with no `--` separator and no hex validation — in [git_diff_tool.py:135-136](coded_tools/sentinel/git_diff_tool.py#L135-L136), [ast_analyzer_tool.py:24](coded_tools/sentinel/ast_analyzer_tool.py#L24), [complexity_metrics_tool.py:28](coded_tools/sentinel/complexity_metrics_tool.py#L28), and [dependency_graph_tool.py:73/81/90](coded_tools/sentinel/dependency_graph_tool.py#L73).

**Exploit:** `base_sha = "--output=<path-to-a-.py-that-gets-imported>"` makes `git diff` write its output to an attacker-named file (`git diff` and `git show` both honor `--output=`). Overwriting a module that is later imported chains toward code execution. Any value beginning with `-` is treated by git as an option, even inside the single `f"{ref}:{path}"` argv element.

**Remediation:** In `run_inputs`, validate `base`/`head` against `^[0-9a-fA-F]{7,40}$` (or a strict ref grammar) and add `"--"` before ref/pathspec arguments in every git call. (The _path_ side of `git show ref:path` is already safe — git rejects `..` in the tree path; only the ref side is the hole.)

### H2 — Live git token persisted cleartext and served to viewers

[`insert_run`, dao.py:32-37](db/dao.py#L32-L37) stores the entire DeliveryEvent verbatim, and `GET /api/v1/runs/{id}` returns `run` (including `event`) to any **viewer**-role caller. The GitHub Action builds `repo.url` as `https://x-access-token:${GH_TOKEN}@github.com/...` ([sentinel-gate.yml:64](.github/workflows/sentinel-gate.yml#L64)), so the token lands in `sentinel.runs.event` in cleartext and in every DB backup/replica. `lib/redact.py` is a logging filter only — it never runs on DB writes or API responses.

**Remediation:** Parse `repo.url` and drop the userinfo (`user:pass@`) before persisting — store only `owner/repo` and keep the authenticated URL in memory for the clone. Do not return the raw `event` blob from `GET /runs`; project only the needed fields.

---

## Medium & Low — notes

- **M1 (transitive deps):** `pip-audit -r requirements.txt` is **clean**, but auditing the _installed_ environment flags `transformers 4.43.3` (numerous advisories incl. deserialization/RCE classes), `urllib3 2.3.0`, `werkzeug 3.1.3`, and `wheel 0.45.1`. `transformers`/`dlib` are pulled by the vendored studio's heavy extras and may not ship in the Gateway image; `urllib3`/`werkzeug` are more likely in the runtime path. **Run `pip-audit` in CI against the fully-resolved lockfile, not just direct pins,** and pin/upgrade the offenders.
- **M2 (test-id injection):** add `"--"` immediately before `*ids` in both the pytest ([:255](coded_tools/sentinel/test_runner_tool.py#L255)) and jest ([:315](coded_tools/sentinel/test_runner_tool.py#L315)) argv.
- **M3 (error leakage):** run `str(e)` through `lib.redact.redact()` before `record_audit`/`bus.publish`, or store a generic message and keep detail in the redacted server log.
- **M4 / M6 (traversal):** confine `added_test_ids` and `run_id` with `os.path.realpath(...).startswith(root)`; reject `.`/`..`/absolute paths.
- **M5 (DB least-privilege):** provision a migration/owner role (used only for `alembic upgrade`) and a DML-only app role (`SELECT/INSERT/UPDATE`, no `DELETE`/DDL); point the Gateway's `DATABASE_URL` at the app role.
- **M7 (DoS):** add `timeout=` to all git subprocess calls, cap bytes read from git output, and skip files above a size threshold in `test_mapper`.
- **L1–L5 (frontend/session):** prefer the existing HttpOnly cookie as the sole credential (drop the `sessionStorage` Bearer), fail the client gate closed on `/healthz` error, set the cookie `SameSite=Strict; Secure` and require a CSRF token or Bearer on all POSTs, and use `hmac.compare_digest` for token checks.
- **L6–L9:** restrict the webhook to `https://`, parse JUnit XML with `defusedxml`, `realpath`-check test files before reading, and wrap the SSE `JSON.parse` in `try/catch`.

---

## What Sentinel already does well

- **SQL injection: none.** Every query uses SQLAlchemy Core with bound parameters, including the JSONB `event['event_id'].astext == event_id` lookup ([dao.py:111](db/dao.py#L111)). The dynamic per-run payload table is resolved through a hard whitelist (`RUN_PAYLOAD_TABLES`), never string-interpolated — `save_run_payload`/`get_payload` cannot be identifier-injected. The `list_runs` HTTP filters (`repo/band/decision/state`) reach `.where()` as bound parameters.
- **The SQLi "money shot" is correctly confined to demo fixtures.** [samples/.../auth/login.py:24](samples/python-payments-service/app/auth/login.py#L24) is parameterized in the baseline; the injectable variant is planted only in a demo diff for the scanner to _detect_. It is not reused in any real Sentinel code path.
- **No shell injection surface.** No `shell=True` anywhere; every subprocess uses list-argv (the residual risk is _argument_ injection, C2/H1/M2 — not shell metacharacter injection).
- **Safe deserialization throughout:** `yaml.safe_load` for all config, `json.load` with no object hooks, no `pickle`/`eval`/`exec`/dynamic import of repo-derived names. AST work uses `ast.parse` (parse-only) and tree-sitter.
- **Frontend XSS: none.** No `dangerouslySetInnerHTML`/`innerHTML`/`eval`/`document.write` in `src/`; all hostile-repo data (findings, diff lines, failure output, agent messages, repo names) renders as escaped React text children. `npm audit`: **0 vulnerabilities**; deps are current majors.
- **Secrets stayed out of git history.** The git-history scan found the live NVIDIA key was **never committed**; `.env` is correctly gitignored and untracked; the only `nvapi-` in history is the fake placeholder in `redact.py`'s self-test. (The exposure is via C1 at runtime, not via the repo.)
- **Run IDs are unguessable** server-generated `uuid4` ([app.py:233](gateway/app.py#L233)) — no `/runs/<id>` enumeration, and no security-by-obscurity on identifiers.
- **The "code decides, LLM only raises risk" invariant holds in code.** `risk_calculator` ignores LLM-passed inputs and only allows a positive escalation; `trust_ladder` enforces a production hard-floor and fails closed; `contract_store` prevents agents from overwriting tool-owned contracts; frontman instructions explicitly mark diff/event content as untrusted data.
- **Defense-in-depth already present:** a secret-scrubbed subprocess environment for test runs, a conservative log `RedactionFilter` on every handler, `install_deps` gated behind an explicit per-repo allow-list, and timeouts on the test/install path.

---

## Prioritized remediation (fix order)

Ordered by _value ÷ effort_, not raw severity — cheap fixes that break the exploit chain come first.

1. **C1 — confine the SPA route** (minutes). Unauthenticated, proven, and the source of the admin-token leak that unlocks everything else. Switch to `StaticFiles(html=True)` or add the resolve-and-contain check. **Then rotate the NVIDIA key, DB password, and API tokens.**
2. **C4 — fix auth config** (minutes). Fail closed when no tokens; replace the documented demo tokens in `.env` with high-entropy secrets. Removes the "everyone is admin" and "admin token is public knowledge" paths.
3. **C2 — lock down the clone** (< 1 hour). `--` separator + `https://`-only scheme allow-list + `GIT_ALLOW_PROTOCOL=https`. Kills RCE-at-intake and the file/SSRF vectors.
4. **H1 + M2 — validate refs, add `--`** (< 1 hour). Hex-validate `base`/`head` in `run_inputs`; add `--` before refs and before `*ids`.
5. **H2 + M3 — stop persisting/echoing secrets** (< 1 hour). Strip userinfo from `repo.url` before storing; redact exception text before audit/SSE; stop returning the raw `event` blob.
6. **M5 — least-privilege DB role** (hours). Split owner/app roles.
7. **C3 — sandbox untrusted test execution** (the real project). Containerize clone/collect/install/test with non-root + `--network none` + read-only workspace + resource limits. Until done, gate `simulate` to a vetted repo allow-list. This is the largest effort and the core risk of the "run on any repo" feature — schedule it deliberately, not last.
8. **M1, M4, M6, M7, L1–L9, I1–I2** — dependency upgrades and the remaining hardening, addressed as normal backlog once the chain above is broken.
