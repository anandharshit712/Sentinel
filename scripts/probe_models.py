"""Probe every NIM model id referenced in config/ with a REAL tool-call request.

Rule 3: NIM retires models without warning, and a dead *fallback* is invisible — nothing fails
until the primary has its first bad minute, then everything fails at once (docs/model-evaluation.md
§8). A catalog listing is not enough: a listed model can still 404 for this account, 410 on EOL, or
answer in prose without ever emitting a tool call, which is useless here because every Sentinel
agent drives coded tools.

    PYTHONPATH=. python scripts/probe_models.py            # ids found in config/
    PYTHONPATH=. python scripts/probe_models.py <model-id>...   # explicit ids

Exit code is the number of dead ids, so CI can gate on it.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
URL = "https://integrate.api.nvidia.com/v1/chat/completions"
TRIALS = 2

# A raw NIM id looks like "vendor/model-name"; the alias keys in custom_llm_info.hocon do not.
_ID = re.compile(r'"([a-z0-9][\w.-]*/[\w.-]+)"')


def config_model_ids() -> list[str]:
    """Every raw NIM id mentioned in the two llm config files, in file order, de-duplicated."""
    ids: list[str] = []
    for name in ("config/llm_config.hocon", "config/custom_llm_info.hocon"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):       # commented-out ids are history, not config
                continue
            for mid in _ID.findall(line):
                if mid not in ids:
                    ids.append(mid)
    return ids


def probe(model: str, key: str) -> tuple[bool, str]:
    """One realistic request: does this model emit a structured tool call, and how fast?"""
    body = {
        "model": model, "max_tokens": 64,
        "messages": [{"role": "user", "content": "Call the tool ping with reason='x'."}],
        "tools": [{"type": "function", "function": {
            "name": "ping", "description": "ping",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}},
                           "required": ["reason"]}}}],
    }
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t = time.time()
    try:
        msg = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]
    except urllib.error.HTTPError as e:
        detail = e.read()[:120].decode(errors="replace")
        return False, f"HTTP {e.code} ({time.time()-t:.1f}s) {detail}"
    except Exception as e:  # timeout, DNS, TLS — all "unusable" for our purposes
        return False, f"{type(e).__name__} ({time.time()-t:.1f}s) {str(e)[:100]}"
    if not msg.get("tool_calls"):
        # Answers, but never calls the tool -> every agent silently stops writing its contract.
        return False, f"no tool_calls ({time.time()-t:.1f}s) — unusable for coded tools"
    return True, f"ok ({time.time()-t:.1f}s)"


def main(argv: list[str]) -> int:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        print("NVIDIA_API_KEY not set (.env)", file=sys.stderr)
        return 1

    ids = argv[1:] or config_model_ids()
    print(f"probing {len(ids)} model id(s), {TRIALS} trial(s) each\n")
    dead = []
    for mid in ids:
        results = [probe(mid, key) for _ in range(TRIALS)]
        good = sum(1 for ok_, _ in results if ok_)
        # Partial failure still matters: a ~8% 500 rate kills a 15-call pipeline run more often
        # than not, so report the ratio rather than a bare pass/fail.
        mark = "OK  " if good == TRIALS else ("FLAKY" if good else "DEAD")
        print(f"  [{mark}] {mid}  {good}/{TRIALS}")
        for ok_, why in results:
            if not ok_:
                print(f"           {why}")
        if not good:
            dead.append(mid)

    print(f"\n{len(ids) - len(dead)}/{len(ids)} usable")
    if dead:
        print("DEAD (replace in config/ before the primary's next bad minute):")
        for mid in dead:
            print(f"  - {mid}")
    return len(dead)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
