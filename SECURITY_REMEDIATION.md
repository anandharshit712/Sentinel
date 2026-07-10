# Sentinel — Security Remediation Plan

**Companion to:** [SECURITY_AUDIT.md](SECURITY_AUDIT.md)
**Date:** 2026-07-10
**Scope:** Fixes for the four Critical (C1–C4) and two High (H1, H2) findings, plus M3 (bundled with H2 — same secret-leak class, one-line fix). Medium/Low items are deferred per request.

> These are **proposed patches**, written to be copy-pasteable against the current tree. Nothing here has been applied to source yet. Apply in the order in §8 (cheap chain-breakers first), verify each with the check given, then run the existing `scripts/verify_c.py` end-to-end.

---

## Fix summary

| #       | Finding                          | Fix in one line                                                     | Effort           | Files touched                                                                       |
| ------- | -------------------------------- | ------------------------------------------------------------------- | ---------------- | ----------------------------------------------------------------------------------- |
| C1      | Path traversal in SPA route      | Resolve + confine served path to `dist/`                            | ~10 min          | `gateway/app.py`                                                                    |
| C2      | Arbitrary `git clone` target     | https-only allow-list + `--` + disable `ext::` + SSRF host block    | ~30 min          | `gateway/app.py`                                                                    |
| C4      | Auth fails open / demo tokens    | Fail closed unless opted in; strong tokens; constant-time compare   | ~20 min          | `gateway/settings.py`, `gateway/app.py`, `.env`                                     |
| H1      | git arg injection via refs       | Strict `base`/`head` validation at the single chokepoint            | ~20 min          | `lib/workspace.py`                                                                  |
| H2 + M3 | Token stored/echoed in cleartext | Strip credentials from `repo.url` before persist; redact error text | ~20 min          | `gateway/app.py`                                                                    |
| C3      | Unsandboxed repo execution       | Stopgap: repo allow-list. Real fix: container sandbox               | 1 day → 1 sprint | `gateway/settings.py`, `gateway/app.py`, `coded_tools/sentinel/test_runner_tool.py` |

Fixing C1→C2→C4→H1→H2 (all small) breaks the full compromise chain. C3 is the one item needing real engineering; ship the stopgap immediately and schedule the sandbox before pointing Sentinel at untrusted repos.

---

## C1 — Confine the SPA route (path traversal)

**Root cause:** [gateway/app.py:390-395](gateway/app.py#L390-L395) builds `FileResponse(_DIST / full_path)` from a user-controlled path with no containment check and no auth, so `GET /../../.env` escapes `dist/`.

**Fix** — resolve the target and verify it stays inside `dist/` before serving:

```python
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "healthz")):
            raise HTTPException(404)
        root = _DIST.resolve()
        target = (root / full_path).resolve()
        # Containment: only ever serve a real file *inside* dist/. Anything else
        # (traversal, missing file, a directory) falls back to the SPA entrypoint.
        if target.is_file() and (target == root or root in target.parents):
            return FileResponse(target)
        return FileResponse(root / "index.html")
```

`Path.resolve()` collapses `..` before the check, so an escaping path can never satisfy `root in target.parents` and drops to `index.html`.

**Verify:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' --path-as-is 'http://localhost:8000/../../.env'   # expect 200 but body = index.html, NOT the .env
curl -s --path-as-is 'http://localhost:8000/../../.env' | head -c 40                          # expect '<!doctype html>...', never 'NVIDIA_API_KEY='
curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8000/'                             # expect 200 (SPA still loads)
```

---

## C2 — Lock down the clone target (RCE / SSRF / file read)

**Root cause:** [gateway/app.py:120-133](gateway/app.py#L120-L133) passes attacker-controlled `event.repo.url` to `git clone` with no scheme allow-list and no `--`, permitting `ext::` transport RCE, `file://` reads, `-`-prefixed option injection, and SSRF.

**Fix** — add `import os`, `import ipaddress`, `import socket`, and `from urllib.parse import urlsplit` at the top of `gateway/app.py`, then:

```python
_ALLOWED_CLONE_SCHEMES = {"https"}


def _validate_clone_url(url: str) -> str:
    """Reject anything that isn't a plain https:// URL to a public host.

    Closes: ext:: transport RCE, file:// local reads, `-`-prefixed option
    injection, and SSRF to internal/loopback/link-local addresses.
    """
    if not url or url.startswith("-"):
        raise ValueError("repo.url is empty or option-like")
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_CLONE_SCHEMES:
        raise ValueError(f"repo.url scheme must be https (got {parts.scheme!r})")
    host = parts.hostname
    if not host:
        raise ValueError("repo.url has no host")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except socket.gaierror as e:
        raise ValueError(f"repo.url host does not resolve: {host}") from e
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("repo.url resolves to a non-public address (SSRF blocked)")
    return url
```

Then in `_clone`, validate the URL and harden the subprocess:

```python
def _clone(event: dict, run_id: str) -> str:
    url = _validate_clone_url(((event.get("repo") or {}).get("url") or "").strip())
    ws = str(workspace.ensure_workspace(run_id))
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(
        ["git", "-c", "protocol.ext.allow=never", "-c", "core.longpaths=true",
         "clone", "--quiet", "--", url, ws],   # '--' stops option parsing
        capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed (exit {r.returncode}): {(r.stderr or r.stdout or '').strip()[:400]}")
    return ws
```

`GIT_ALLOW_PROTOCOL=https` + `protocol.ext.allow=never` disable `ext::`/`file://`/`ssh://` even if validation is bypassed (defense in depth); `--` stops `-`-prefixed URLs being read as flags. The GitHub Action's `https://x-access-token:…@github.com/…` still passes (https + public host).

> **Residual (accept or track):** the DNS resolve is a TOCTOU check vs. git's own resolution (DNS-rebinding). The scheme/transport controls are the real RCE fix; the host block is best-effort SSRF defense. For stronger SSRF isolation, run the clone inside the C3 sandbox with `--network` restricted to the git host.

**Verify** — `simulate` must reject these (HTTP 400 / run `failed`, no clone, no command run):

```bash
for u in 'ext::sh -c touch /tmp/pwned' 'file:///etc/passwd' '--upload-pack=touch /tmp/x' 'http://169.254.169.254/' 'http://localhost:5432/'; do
  curl -s -X POST localhost:8000/api/v1/simulate -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
    -d "{\"event\":{\"event_id\":\"t\",\"repo\":{\"url\":\"$u\",\"name\":\"x/y\"},\"change\":{\"base_sha\":\"HEAD~1\",\"head_sha\":\"HEAD\"},\"target_transition\":{\"from_env\":\"dev\",\"to_env\":\"test\"}}}"
done   # each -> 400/failed; verify /tmp/pwned was NOT created
```

---

## C4 — Fail closed + strong tokens + constant-time compare

**Root cause:** [gateway/settings.py:38](gateway/settings.py#L38) makes `OPEN_MODE` (everyone = admin) the default whenever `API_TOKENS` is unset, and the shipped `.env` uses the demo tokens printed in the public [settings.py docstring](gateway/settings.py#L4). Token match is also a non-constant-time dict lookup ([app.py:101](gateway/app.py#L101), L3).

**Fix 1 — fail closed** in `gateway/settings.py`:

```python
API_TOKENS = _parse_tokens()

# Open mode (every request is admin) is now OPT-IN, never the silent default.
# With no tokens AND no explicit opt-in, privileged routes are simply unauthorized.
OPEN_MODE = (os.environ.get("SENTINEL_OPEN_MODE", "").lower() in ("1", "true", "yes")
             and not API_TOKENS)
```

**Fix 2 — constant-time compare** in `gateway/app.py` (add `import hmac`), replacing `_role_for`:

```python
def _role_for(token: str | None) -> str | None:
    """Resolve a bare token to its role. OPEN_MODE -> admin; unknown token -> None."""
    if settings.OPEN_MODE:
        return "admin"
    tok = (token or "").strip()
    for known, role in settings.API_TOKENS.items():
        if hmac.compare_digest(known, tok):
            return role
    return None
```

**Fix 3 — rotate `.env` to real secrets** (never the documented values):

```bash
python - <<'PY'
import secrets
print("API_TOKENS=%s:admin,%s:approver,%s:viewer" % (
    secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)))
PY
# paste into .env, and rotate NVIDIA_API_KEY + the Postgres password too (assume C1 leaked them)
```

**Verify:**

```bash
unset -v API_TOKENS  # simulate a misconfigured deploy (no tokens, no opt-in)
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/v1/simulate -d '{}'   # expect 401, NOT 202
# with tokens set:
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/v1/runs -H 'Authorization: Bearer admintok'  # expect 401 (demo token dead)
```

---

## H1 — Validate git refs at the single chokepoint

**Root cause:** `base`/`head` reach `git diff <base> <head>` and `git show <ref>:<path>` unvalidated (across [git_diff_tool.py:135-136](coded_tools/sentinel/git_diff_tool.py#L135-L136), [ast_analyzer_tool.py:24](coded_tools/sentinel/ast_analyzer_tool.py#L24), [complexity_metrics_tool.py:28](coded_tools/sentinel/complexity_metrics_tool.py#L28), [dependency_graph_tool.py:73/81/90](coded_tools/sentinel/dependency_graph_tool.py#L73)), so `base_sha="--output=…"` becomes a git option (arbitrary file write). All of them draw the ref from the one helper `run_inputs`, so validating there fixes every call site at once.

**Fix** — in [lib/workspace.py](lib/workspace.py), add a ref validator and apply it in `run_inputs`:

```python
# git refs the pipeline legitimately uses: a hex SHA (7-64) or HEAD / HEAD~N.
# Anything else (esp. a leading '-' or '..') is rejected so it can't be read as a git option.
_REF_RE = re.compile(r"^(?:[0-9a-fA-F]{7,64}|HEAD(?:~\d+)?)$")


def valid_ref(ref):
    if ref is None:
        return None
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise ValueError(f"unsafe git ref: {ref!r}")
    return ref
```

Then in `run_inputs`, wrap the two refs:

```python
    return (
        ws,
        valid_ref(args.get("base_ref") or change.get("base_sha")),
        valid_ref(args.get("head_ref") or change.get("head_sha")),
        args.get("repo_name") or repo.get("name"),
    )
```

Every tool already wraps `invoke` in `try/except` returning `"Error: …"`, so a hostile ref becomes a clean stage failure instead of a shell primitive. `dependency_graph`'s `head = head or "HEAD"` still works (`HEAD` matches `_REF_RE`).

> If real symbolic branch names ever need to flow through, widen `_REF_RE` deliberately (e.g. add `[\w./-]+` but keep the leading-`-` and `..` rejections) — do not remove the anchor.

**Verify:**

```bash
python -c "from lib.workspace import valid_ref; \
  [print('ok', r) for r in ('a1b2c3d','HEAD','HEAD~2')]; \
  import pytest; \
  [__import__('sys').exit('FAIL: accepted '+repr(r)) for r in ('--output=x','a1b2c3;rm','..','-x') if _try(r)]" 2>/dev/null || \
python - <<'PY'
from lib.workspace import valid_ref
for good in ("a1b2c3d", "HEAD", "HEAD~2"): assert valid_ref(good) == good
for bad in ("--output=/tmp/x", "..", "-x", "a b", "ext::sh"):
    try: valid_ref(bad); raise SystemExit(f"FAIL accepted {bad!r}")
    except ValueError: pass
print("valid_ref OK")
PY
```

---

## H2 + M3 — Stop persisting and echoing credentials

**Root cause:** [`insert_run`, dao.py:32-37](db/dao.py#L32-L37) stores the full event verbatim (including `repo.url` with an embedded git token from [sentinel-gate.yml:64](.github/workflows/sentinel-gate.yml#L64)), and `GET /runs/{id}` returns it to any viewer. Separately (M3), [app.py:192-193](gateway/app.py#L192-L193) writes raw `str(e)` — which can contain the tokenized URL from git stderr — into the audit log and SSE stream, bypassing `lib/redact.py`.

**Fix 1 — redact the URL before persisting** (add `import copy` and `from urllib.parse import urlsplit, urlunsplit` to `gateway/app.py`):

```python
def _redact_url(url: str) -> str:
    """Drop userinfo (user:pass@) from a URL so tokens aren't persisted."""
    try:
        p = urlsplit(url)
        if p.username or p.password:
            netloc = p.hostname or ""
            if p.port:
                netloc += f":{p.port}"
            return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    except Exception:
        pass
    return url
```

In `_start_run`, persist a redacted copy but hand the real event to the pipeline:

```python
def _start_run(event: dict, ws_override: str | None) -> str:
    tt = event["target_transition"]
    run_id = str(uuid.uuid4())
    stored = copy.deepcopy(event)                       # DB/API copy: no credentials
    repo = stored.get("repo") or {}
    if repo.get("url"):
        repo["url"] = _redact_url(repo["url"])
    dao.insert_run(run_id, event=stored, source=event.get("source", "manual"),
                   repo=event["repo"]["name"], from_env=tt["from_env"], to_env=tt["to_env"])
    dao.record_audit(run_id, actor="gateway", action="run_received",
                     payload={"event_id": event["event_id"]})
    task = asyncio.create_task(_run_pipeline(run_id, event, ws_override))  # REAL event -> clone
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return run_id
```

**Fix 2 (M3) — redact error text** before it hits the audit log / SSE. In `_run_pipeline`'s `except` (add `from lib.redact import redact`):

```python
    except Exception as e:
        msg = redact(str(e))[:500]
        dao.set_run_state(run_id, "failed", finished=True)
        dao.record_audit(run_id, actor="gateway", action="run_failed", payload={"error": msg})
        bus.publish(run_id, {"kind": "state_change", "state": "failed", "error": msg})
```

`redact()` already scrubs `scheme://user:PASSWORD@host`, so a tokenized URL surfacing via git stderr is masked in both sinks.

**Verify:**

```bash
# submit a run whose repo.url carries a fake token, then confirm it never comes back out
curl -s -X POST localhost:8000/api/v1/simulate -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"event":{"event_id":"h2","repo":{"url":"https://x-access-token:SECRET123@github.com/o/r.git","name":"o/r"},"change":{"base_sha":"HEAD~1","head_sha":"HEAD"},"target_transition":{"from_env":"dev","to_env":"test"}}}'
curl -s localhost:8000/api/v1/runs/<run_id> -H "Authorization: Bearer $ADMIN" | grep -c SECRET123   # expect 0
psql "$DATABASE_URL" -c "select event->'repo'->>'url' from sentinel.runs where run_id='<run_id>'"    # expect no token
```

---

## C3 — Contain untrusted repo execution

**Root cause:** [test_runner_tool.py](coded_tools/sentinel/test_runner_tool.py) runs (and even _collects_) the cloned repo's pytest/jest, executing `conftest.py`/`jest.config.js` as the Gateway user with full FS + network — on every run. Env scrubbing and the `install_deps` gate help but provide no isolation.

This is two deliverables: a **stopgap you can ship today** and the **real sandbox**.

### C3a — Stopgap: repo allow-list (ship now, ~30 min)

Only run repos an operator has vetted. In `gateway/settings.py`:

```python
# Repos permitted to run through simulate. Empty -> allow all (dev only; pair with SENTINEL_OPEN_MODE).
ALLOWED_REPOS = {r.strip() for r in os.environ.get("SENTINEL_REPO_ALLOWLIST", "").split(",") if r.strip()}
```

In `simulate`, after `_validate_event`:

```python
    if settings.ALLOWED_REPOS and event["repo"]["name"] not in settings.ALLOWED_REPOS:
        raise HTTPException(403, "repo not on the Sentinel allow-list")
```

Set `SENTINEL_REPO_ALLOWLIST` to the repos in `config/repo_config.yaml`. Combined with C2 (https-only) and C4 (real admin token), this shrinks the untrusted-code surface to a known set while the sandbox is built.

### C3b — Real fix: run clone/collect/install/test in a sandbox

Execute every step that touches repo code inside a locked-down container. Sketch of a helper the test/collect/install calls route through:

```python
# coded_tools/sentinel/_sandbox.py  (new)
def run_sandboxed(argv, workspace, timeout, writable=("/out",)):
    """Run argv inside a throwaway container: no network, non-root, read-only workspace."""
    docker = [
        "docker", "run", "--rm",
        "--network", "none",              # no egress (blocks SSRF/exfil from test code)
        "--user", "65534:65534",          # nobody
        "--read-only",                    # immutable rootfs
        "-v", f"{workspace}:/repo:ro",    # repo is read-only to the test process
        "--tmpfs", "/tmp:size=256m",
        "--tmpfs", "/out:size=64m",       # junit/jest json written here, copied out after
        "--memory", "2g", "--pids-limit", "512", "--cpus", "2",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-w", "/repo",
        "sentinel-runner:latest",         # image with python+pytest+node+jest preinstalled
        *argv,
    ]
    return subprocess.run(docker, capture_output=True, text=True, timeout=timeout)
```

Integration notes:

- Point the JUnit/jest `--outputFile` at the writable `/out` mount and read results from there (the workspace itself is `-v …:ro`).
- `install_deps` (pip/npm) needs network — run the _install_ in a separate, egress-restricted container (allow-list the package registry only) or pre-bake deps; keep the _test_ container `--network none`.
- On non-Docker hosts, the equivalent controls are a dedicated low-priv OS user + `firejail`/`bubblewrap` + `ulimit` + an egress-deny firewall rule.
- Backport this decision to `01-proposed-solution.md` §9 (framework-forced design deviation), consistent with the repo's convention.

**Verify:** a repo whose `conftest.py` contains `open("/etc/passwd").read()` / an outbound `socket.connect(...)` / a write to `/repo/x` must fail those operations (permission denied / network unreachable / read-only FS) while a benign suite still reports pass/fail normally.

---

## Apply order & checklist

Ordered by value ÷ effort — each early step removes a rung of the exploit ladder:

1. **C1** — confine SPA route → stops the unauthenticated `.env` leak (kills the entry point).
2. **C4** — fail closed + rotate `.env` tokens/keys → removes trivial admin.
3. **C2** — clone allow-list + `--` + `ext::` off → kills RCE-at-intake and SSRF.
4. **H1** — `valid_ref` in `run_inputs` → kills git arg-injection file write.
5. **H2 + M3** — redact URL before persist + redact error text → stops token leakage to viewers/logs.
6. **C3a** — repo allow-list → bound the untrusted-code surface immediately.
7. **C3b** — container sandbox → the real containment; schedule before opening `simulate` to arbitrary repos.

**Regression gate after each change:**

- [ ] `python -m pytest` (unit suite green)
- [ ] `python scripts/verify_c.py` (both demos: happy → promote, insecure → escalate → approve)
- [ ] `python -m bandit -r gateway coded_tools/sentinel lib db` (no new High/Medium)
- [ ] Manual PoCs in each §Verify block above return the expected safe result.

**Post-fix hygiene (do once):** rotate `NVIDIA_API_KEY`, the Postgres password, and all API tokens; confirm the running Gateway is not reachable on a public interface without the new tokens.
