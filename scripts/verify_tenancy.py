"""Phase 4 end to end: two real accounts, signed in through Keycloak, cannot see each other.

Needs Keycloak (deploy/keycloak, realm `sentinel`) and the Gateway running with AUTH_MODE=oidc:

    KEYCLOAK_ADMIN=admin KEYCLOAK_ADMIN_PASSWORD=... GATEWAY_URL=http://localhost:8000 \\
    PYTHONPATH=. python scripts/verify_tenancy.py

What is real and what is seeded:
  real    Keycloak users, the browser sign-in flow (login form -> /auth/callback -> session cookie),
          every API call below, row-level security, the CI API-key path
  seeded  the code-host link (GitHub/GitLab OAuth needs a human at a browser; proven separately)
          and the runs/findings rows (a pipeline run needs the LLM; isolation is about rows)
"""
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dotenv import load_dotenv

load_dotenv()

from db import accounts, dao  # noqa: E402
from gateway import auth  # noqa: E402

GW = os.environ.get("GATEWAY_URL", "http://localhost:8000").rstrip("/")
KC = auth.KC_INTERNAL


def _req(opener, url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    h = {"Content-Type": "application/json"} if isinstance(data, (dict, list)) else {}
    r = urllib.request.Request(url, data=body, headers={**h, **(headers or {})}, method=method)
    try:
        with opener.open(r, timeout=30) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _admin_token() -> str:
    data = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": os.environ["KEYCLOAK_ADMIN"],
                                   "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    with urllib.request.urlopen(f"{KC}/realms/master/protocol/openid-connect/token", data, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def _create_user(email: str, password: str) -> None:
    tok = _admin_token()
    body = {"username": email, "email": email, "enabled": True, "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}]}
    req = urllib.request.Request(f"{KC}/admin/realms/{auth.REALM}/users", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    urllib.request.urlopen(req, timeout=30).close()


def _sign_in(email: str, password: str):
    """The browser flow, without a browser: /auth/login -> Keycloak form -> /auth/callback."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, page = _req(op, f"{GW}/auth/login")
    m = re.search(r'action="([^"]+)"', page)
    assert m, f"no Keycloak login form (status {status})"
    action = m.group(1).replace("&amp;", "&")
    form = urllib.parse.urlencode({"username": email, "password": password}).encode()
    status, _ = _req(op, action, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert any(c.name == "sentinel_session" for c in jar), f"no session cookie after sign-in ({status})"
    return op


def main() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and cond

    tag = uuid.uuid4().hex[:8]
    users = {k: (f"tenancy-{k}-{tag}@example.com", "Tenancy-check-" + uuid.uuid4().hex) for k in "ab"}
    for email, pw in users.values():
        _create_user(email, pw)
    ops = {k: _sign_in(*users[k]) for k in users}
    check(True, "two Keycloak users signed in through /auth/login -> /auth/callback")

    st, body = _req(ops["a"], f"{GW}/api/v1/runs")
    check(st == 403 and "link_required" in body, f"unlinked account is held at the link gate ({st})")

    me = {k: json.loads(_req(ops[k], f"{GW}/api/v1/whoami")[1])["account"]["id"] for k in ops}
    for k, aid in me.items():          # seeded link (see module docstring)
        accounts.link_identity(aid, "github", f"seed-{k}-{tag}", f"seed-{k}")

    seeded = {}
    for k, aid in me.items():
        with dao.scoped(aid):
            repo = accounts.create_repo(aid, host="other", full_name=f"t/{k}{tag}",
                                        clone_url=f"https://example.com/t/{k}{tag}", host_repo_id=None,
                                        status="active", verified_via="operator")
            rid = str(uuid.uuid4())
            ev = {"event_id": f"ten-{k}-{tag}", "repo": {"name": f"ten-{k}", "url": repo["clone_url"]},
                  "target_transition": {"from_env": "dev", "to_env": "test"}}
            dao.insert_run(rid, ev, "manual", f"ten-{k}", "dev", "test", repo_id=repo["id"])
            dao.save_findings(rid, [{"finding_id": "F1", "kind": "security", "severity": "low",
                                     "payload": {"id": "F1", "title": "x"}, "fingerprint": "fp",
                                     "source": "tool"}])
            fid = dao.list_findings(rid)[0]["id"]
            _kid, key = accounts.create_repo_key(repo["id"])
        seeded[k] = {"run": rid, "repo": repo, "finding": fid, "key": key}

    runs_a = {r["run_id"] for r in json.loads(_req(ops["a"], f"{GW}/api/v1/runs?size=200")[1])["runs"]}
    check(seeded["a"]["run"] in runs_a and seeded["b"]["run"] not in runs_a, "A's run list holds only A's runs")
    st, _ = _req(ops["a"], f"{GW}/api/v1/runs/{seeded['b']['run']}")
    check(st == 404, f"A fetching B's run by direct id -> 404 ({st})")
    st, _ = _req(ops["a"], f"{GW}/api/v1/findings/{seeded['b']['finding']}/verdict", {"verdict": "real"})
    check(st == 404, f"A judging B's finding by direct id -> 404 ({st})")
    repos_a = {r["id"] for r in json.loads(_req(ops["a"], f"{GW}/api/v1/repos")[1])["repos"]}
    check(repos_a == {seeded["a"]["repo"]["id"]}, "A's repo list holds only A's repo")
    st, _ = _req(ops["a"], f"{GW}/api/v1/admin/accounts")
    check(st == 403, f"a non-admin account cannot list accounts ({st})")

    ev = {"event_id": f"ci-{tag}", "source": "jenkins",
          "repo": {"name": "x", "url": seeded["a"]["repo"]["clone_url"]},
          "change": {"base_sha": "a", "head_sha": "b"}, "target_transition": {"from_env": "dev", "to_env": "test"}}
    st, body = _req(urllib.request.build_opener(), f"{GW}/api/v1/ci/runs", {"event": ev},
                    headers={"X-Sentinel-Key": seeded["b"]["key"]})
    check(st == 403 and "does not match" in body, f"B's CI key cannot start a run on A's repo ({st})")

    accounts.set_account_status(me["a"], "suspended")
    st, _ = _req(ops["a"], f"{GW}/api/v1/runs")
    check(st == 403, f"a suspended account is refused on its next request ({st})")
    accounts.set_account_status(me["a"], "active")

    print("verify_tenancy:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
