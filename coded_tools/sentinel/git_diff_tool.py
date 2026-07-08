"""git_diff_tool (04 §5.1, A1) — unified diff + changed-file list for a base..head range.

First step of the change-analysis pipeline. Seeds the work-in-progress ChangeProfile in
sly_data (`change_profile_wip`): files (path, language, change_type, hunks), loc counts and a
heuristic classification. ast_analyzer then fills functions_changed; dependency_graph finalizes.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Any, Dict, List, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.git_diff")

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_EXT_LANG = {".py": "python", ".js": "javascript", ".jsx": "javascript",
             ".ts": "typescript", ".tsx": "typescript"}
_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added"}


def _run_git(repo: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _language(path: str) -> str:
    dot = path.rfind(".")
    return _EXT_LANG.get(path[dot:].lower(), "other") if dot != -1 else "other"


def _classify(paths: List[str]) -> str:
    # ponytail: heuristic classification; bug_fix/refactor need semantics we don't infer here.
    def kind(p: str) -> str:
        pl = p.lower()
        if pl.endswith(("requirements.txt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                        ".json", ".lock")) or "dockerfile" in pl:
            return "config"
        if pl.endswith((".md", ".rst", ".txt")) or pl.startswith("docs/") or "/docs/" in pl:
            return "docs"
        return "code"
    kinds = {kind(p) for p in paths}
    if kinds == {"docs"}:
        return "docs"
    if kinds == {"config"}:
        return "config"
    if kinds == {"code"}:
        return "feature"
    return "mixed"


def _parse_name_status(text: str) -> Dict[str, Dict[str, Any]]:
    """new_path -> {change_type, old_path?}."""
    out: Dict[str, Dict[str, Any]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0]
        change_type = _STATUS.get(code[0], "modified")
        if code[0] in ("R", "C") and len(parts) >= 3:
            out[parts[2]] = {"change_type": change_type, "old_path": parts[1]}
        else:
            out[parts[1]] = {"change_type": change_type}
    return out


def _parse_unified(text: str) -> Dict[str, Dict[str, Any]]:
    """new_path -> {hunks, loc_added, loc_removed, is_binary}."""
    files: Dict[str, Dict[str, Any]] = {}
    cur: Dict[str, Any] = None
    cur_path: str = None
    hunk: Dict[str, Any] = None

    def close_hunk() -> None:
        if hunk is not None and cur is not None:
            cur["hunks"].append(hunk)

    for line in text.splitlines():
        if line.startswith("diff --git "):
            close_hunk()
            hunk = None
            cur = {"hunks": [], "loc_added": 0, "loc_removed": 0, "is_binary": False}
            cur_path = None
        elif line.startswith("+++ "):
            p = line[4:]
            if p != "/dev/null":
                cur_path = p[2:] if p.startswith("b/") else p
                files[cur_path] = cur
        elif line.startswith("--- ") and cur_path is None:
            p = line[4:]
            if p != "/dev/null":  # deleted file: name comes from the a/ side
                cur_path = p[2:] if p.startswith("a/") else p
                files[cur_path] = cur
        elif line.startswith("Binary files "):
            if cur is not None:
                cur["is_binary"] = True
                # binary path may never hit a +++ line; recover it here
                if cur_path is None:
                    m = re.search(r" b/(.+?) differ$", line) or re.search(r" a/(.+?) and", line)
                    if m:
                        cur_path = m.group(1)
                        files[cur_path] = cur
        elif line.startswith("@@"):
            close_hunk()
            m = _HUNK_RE.match(line)
            if m and cur is not None:
                hunk = {"old_start": int(m.group(1)), "old_lines": int(m.group(2) or 1),
                        "new_start": int(m.group(3)), "new_lines": int(m.group(4) or 1),
                        "patch": line}
        elif hunk is not None and line and line[0] in "+-" and not line.startswith(("+++", "---")):
            hunk["patch"] += "\n" + line
            if line[0] == "+":
                cur["loc_added"] += 1
            else:
                cur["loc_removed"] += 1
    close_hunk()
    return files


class GitDiffTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            repo = args["repo_path"]
            base, head = args["base_ref"], args["head_ref"]
            status = _parse_name_status(_run_git(repo, "diff", "--name-status", "-M", base, head))
            diffs = _parse_unified(_run_git(repo, "diff", "-U0", "-M", base, head))

            files: List[Dict[str, Any]] = []
            loc_added = loc_removed = 0
            for path, st in status.items():
                d = diffs.get(path, {"hunks": [], "loc_added": 0, "loc_removed": 0, "is_binary": False})
                loc_added += d["loc_added"]
                loc_removed += d["loc_removed"]
                entry: Dict[str, Any] = {
                    "path": path,
                    "language": _language(path),
                    "change_type": st["change_type"],
                    "hunks": d["hunks"],
                    "functions_changed": [],
                }
                if d["is_binary"]:
                    entry["is_binary"] = True
                if "old_path" in st:
                    entry["old_path"] = st["old_path"]
                files.append(entry)

            profile = {
                "files": files,
                "loc_added": loc_added,
                "loc_removed": loc_removed,
                "classification": _classify([f["path"] for f in files]),
                "new_functions": [],
            }
            sly_data["change_profile_wip"] = profile
            logger.info("run %s: git_diff %d files +%d/-%d", run_id, len(files), loc_added, loc_removed)
            return {"files": [f["path"] for f in files], "loc_added": loc_added,
                    "loc_removed": loc_removed, "count": len(files)}
        except Exception as e:  # never raise through the framework
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
