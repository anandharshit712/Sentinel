"""Fire a Sentinel run at a running Gateway for ANY public git repo — no GitHub Action needed.

    PYTHONPATH=. python scripts/run_repo.py https://github.com/owner/repo
    PYTHONPATH=. python scripts/run_repo.py https://github.com/owner/repo --base <sha> --head <sha>

With no --base/--head it shallow-clones the repo to use the last two commits on the default branch
(base=HEAD~1, head=HEAD). The Gateway does its own full clone of the URL to run the pipeline; this
local clone only reads the SHAs. repo.name is derived as owner/repo so it matches repo_config keys
(e.g. install_deps). Prints the dashboard run URL and polls until the run finishes.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request


def _repo_name(url: str) -> str:
    tail = url.rstrip("/").removesuffix(".git")
    parts = tail.replace(":", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def _last_two_shas(url: str) -> tuple[str, str]:
    ws = tempfile.mkdtemp(prefix="sentinel-shas-")
    subprocess.run(["git", "clone", "--quiet", "--depth", "2", url, ws], check=True,
                   capture_output=True, text=True)
    log = subprocess.run(["git", "-C", ws, "log", "-2", "--format=%H"],
                         capture_output=True, text=True, check=True).stdout.split()
    if len(log) < 2:
        sys.exit("repo has fewer than 2 commits — pass --base/--head explicitly")
    head, base = log[0], log[1]  # newest first
    return base, head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="public git URL, e.g. https://github.com/owner/repo")
    ap.add_argument("--base", help="base commit SHA (default: HEAD~1)")
    ap.add_argument("--head", help="head commit SHA (default: HEAD)")
    ap.add_argument("--from-env", default="dev")
    ap.add_argument("--to-env", default="test")
    ap.add_argument("--name", help="repo.name (default: owner/repo from URL)")
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--token", default=os.environ.get("SENTINEL_TOKEN", ""),
                    help="Bearer token if the Gateway isn't in OPEN_MODE (or set SENTINEL_TOKEN)")
    a = ap.parse_args()

    base, head = (a.base, a.head) if (a.base and a.head) else _last_two_shas(a.url)
    name = a.name or _repo_name(a.url)
    event = {
        "event_id": f"cli-{int(time.time() * 1000)}", "source": "manual",
        "repo": {"url": a.url, "name": name, "default_branch": "main"},
        "change": {"base_sha": base, "head_sha": head, "branch": "cli", "title": "cli run", "author": "cli"},
        "target_transition": {"from_env": a.from_env, "to_env": a.to_env},
        "requested_by": "cli",
    }
    body = json.dumps({"event": event}).encode()
    headers = {"Content-Type": "application/json"}
    if a.token:
        headers["Authorization"] = f"Bearer {a.token}"
    req = urllib.request.Request(f"{a.gateway}/api/v1/simulate", body, headers)
    run_id = json.load(urllib.request.urlopen(req))["run_id"]
    print(f"\n  repo={name} base={base[:8]} head={head[:8]} {a.from_env}->{a.to_env}")
    print(f"  watch: {a.gateway}/runs/{run_id}\n")

    detail_url = f"{a.gateway}/api/v1/runs/{run_id}"
    dreq = urllib.request.Request(detail_url, headers=headers)
    for _ in range(300):
        d = json.load(urllib.request.urlopen(dreq))
        state = d["run"]["state"]
        print(f"  state={state:10}", end="\r")
        if state in ("done", "failed"):
            dec = (d.get("decision") or {}).get("decision")
            risk = (d.get("risk_score") or {}).get("score")
            crit = ((d.get("review_report") or {}).get("counts") or {}).get("critical")
            print(f"\n  {state}: decision={dec} risk={risk} criticals={crit}")
            return 0 if state == "done" else 1
        time.sleep(2)
    print("\n  timeout")
    return 1


if __name__ == "__main__":
    sys.exit(main())
