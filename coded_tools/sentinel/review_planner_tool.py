"""review_planner_tool (04 §5.18, A8) — deterministically sizes the security review fan-out.

Runs first in the review stage (no LLM). It reads the ChangeProfile, excludes vendored/generated
files, scores the remaining added lines for hotspot risk (`lib/triage`), and sizes the review:

    shard_count = clamp(ceil(hotspot_lines / review_budget_lines), 1, max_review_shards)

then partitions the in-scope files into that many weight-balanced shards, writes the `review_plan`
contract to sly_data, and RETURNS the exact `security_reviewer_n` names for the frontman to invoke
as one parallel batch. `mode = audit` when the base SHA is the git empty-tree (full-repo audit,
01 §12.1) else `pr`. Everything here is deterministic — the LLM never decides how much to spend.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts, triage

logger = logging.getLogger("coded_tools.review_planner")


class ReviewPlannerTool(CodedTool):
    def __init__(self, repo_config_path: str = "config/repo_config.yaml"):
        self.repo_config_path = repo_config_path

    def _cfg(self, repo: Optional[str]) -> Dict[str, Any]:
        try:
            with open(self.repo_config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}
        defaults = cfg.get("defaults") or {}
        rc = (cfg.get("repos") or {}).get(repo) or {}
        return {
            "budget": int(rc.get("review_budget_lines", defaults.get("review_budget_lines", 800))),
            "max_shards": int(rc.get("max_review_shards", defaults.get("max_review_shards", 4))),
            "exclude_globs": rc.get("exclude_globs", defaults.get("exclude_globs", [])) or [],
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            profile = sly_data.get("change_profile") or sly_data.get("change_profile_wip") or {}
            event = sly_data.get("event") or {}
            repo = (event.get("repo") or {}).get("name")
            base_sha = ((event.get("change") or {}).get("base_sha") or "").strip()
            mode = "audit" if base_sha == triage.EMPTY_TREE else "pr"
            cfg = self._cfg(repo)
            budget, max_shards, extra_globs = cfg["budget"], cfg["max_shards"], cfg["exclude_globs"]

            sensitive_files = set()
            for sf in profile.get("sensitive_flags", []):
                sensitive_files.update(sf.get("files", []))

            scored = triage.scored_lines(triage.iter_added_lines(profile), sensitive_files, extra_globs)
            hotspot_lines = sum(1 for d in scored if d["score"] > 0)

            # per-file weight = sum of positive hotspot scores; every in-scope file is included so
            # zero-hotspot files still get scanned for secrets by their shard reviewer.
            weights: Dict[str, float] = {}
            for d in scored:
                weights[d["file"]] = weights.get(d["file"], 0.0) + max(0.0, d["score"])
            in_scope = sorted(weights)

            all_with_added = {p for p, _ln, _c in triage.iter_added_lines(profile)}
            excluded_files = len(all_with_added - set(in_scope))

            shard_count = min(max_shards, max(1, math.ceil(hotspot_lines / budget))) if budget > 0 else 1
            shard_count = max(1, min(shard_count, len(in_scope) or 1))

            partitions = _partition(weights, shard_count)
            shards = []
            for i, files in enumerate(partitions, start=1):
                shards.append({"shard": i, "label": _label(files),
                               "files": files, "hotspot_weight": round(sum(weights[f] for f in files), 3)})

            metrics = {"files_scanned": len(in_scope), "excluded_files": excluded_files,
                       "added_lines": len(scored), "hotspot_lines": hotspot_lines,
                       "shard_count": shard_count, "basis": "ceil(hotspot_lines / review_budget_lines)"}
            payload = {"mode": mode, "budget_lines": budget, "shards": shards, "metrics": metrics}
            wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by="review_planner")
            contracts.validate("review_plan", wrapped)
            sly_data["review_plan"] = wrapped

            agents = [f"security_reviewer_{i}" for i in range(1, shard_count + 1)]
            logger.info("run %s: review_planner mode=%s shards=%d hotspots=%d files=%d",
                        run_id, mode, shard_count, hotspot_lines, len(in_scope))
            return {"shard_count": shard_count, "mode": mode,
                    "agents_to_invoke": agents, "metrics": metrics}
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)


def _label(files: List[str]) -> str:
    """Most common top-level path segment among the shard's files (or 'root')."""
    if not files:
        return "empty"
    tops: Dict[str, int] = {}
    for f in files:
        top = f.replace("\\", "/").split("/")[0]
        tops[top] = tops.get(top, 0) + 1
    return max(sorted(tops), key=lambda t: tops[t])


def _partition(weights: Dict[str, float], shard_count: int) -> List[List[str]]:
    """Split files into `shard_count` weight-balanced bins (deterministic, every file assigned once).

    Files are sorted heaviest-first (path tiebreak) and greedily placed in the currently-lightest
    bin. Path-sorted assignment keeps same-directory files adjacent, so bins stay roughly modular.
    """
    if shard_count <= 1:
        return [sorted(weights)]
    files = sorted(weights, key=lambda f: (-weights[f], f))
    bins: List[List[str]] = [[] for _ in range(shard_count)]
    load = [0.0] * shard_count
    for f in files:
        i = min(range(shard_count), key=lambda k: (load[k], k))
        bins[i].append(f)
        load[i] += weights[f]
    return [sorted(b) for b in bins]


def demo() -> None:
    tool = ReviewPlannerTool()

    def _profile(files):  # files: {path: [added lines]}
        return {"files": [{"path": p, "sensitive_flags": [],
                           "hunks": [{"new_start": 1, "patch": "@@\n" + "".join("+" + l + "\n" for l in lines)}]}
                          for p, lines in files.items()],
                "sensitive_flags": []}

    # tiny PR-ish change → 1 shard, mode pr
    sly = {"run_id": "t", "event": {"repo": {"name": "x"}, "change": {"base_sha": "abc123"}},
           "change_profile": _profile({"calc.py": ["def sub(a, b):", "    return a - b"]})}
    out = tool.invoke({}, sly)
    assert out["shard_count"] == 1 and out["mode"] == "pr", out
    assert out["agents_to_invoke"] == ["security_reviewer_1"]
    assert contracts.is_valid("review_plan", sly["review_plan"])

    # empty-tree base → audit mode
    sly2 = {"run_id": "t", "event": {"repo": {"name": "x"}, "change": {"base_sha": triage.EMPTY_TREE}},
            "change_profile": _profile({"a.py": ["x = 1"]})}
    assert tool.invoke({}, sly2)["mode"] == "audit"

    # partition: 6 files, 3 shards → every file assigned once, balanced, deterministic
    w = {f"m{i}/f.py": float(i + 1) for i in range(6)}
    p = _partition(w, 3)
    assert sorted(f for b in p for f in b) == sorted(w), "every file assigned once"
    assert _partition(w, 3) == p, "partition not deterministic"
    assert all(b for b in p), "no empty bin when files >= shards"
    print("review_planner OK")


if __name__ == "__main__":
    demo()
