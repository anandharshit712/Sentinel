"""Running a JS/TS test, and Stryker over one function, for one repository (Phase 6).

The Node twin of `lib.pyexec`: what `lib.evaluate` and `lib.differential` need to run a proposed
Jest test against unmodified code and then measure it with Stryker. Every call that executes
repository code goes through `lib.sandbox`.

Tooling: Stryker is installed into the run's workspace with `npm install --no-save --ignore-scripts`
(network on — the sandbox install phase). `--no-save` leaves package.json and the lockfile alone;
`--ignore-scripts` means installing runs no lifecycle script, the repository's included. On a fresh
clone the same command also installs the repository's own dependencies, which the test needs.

    python -m lib.jsexec        # self-check (needs node + npm; installs nothing)
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from lib import js_imports, sandbox, ts_parse

STRYKER_VERSION = "10.1.1"
STRYKER_PKGS = (f"@stryker-mutator/core@{STRYKER_VERSION}",
                f"@stryker-mutator/jest-runner@{STRYKER_VERSION}")
TEST_RE = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$", re.I)
JEST_TIMEOUT = 180
STRYKER_TIMEOUT = 900
INSTALL_TIMEOUT = 600
CAUGHT = {"Killed", "Timeout"}
MISSED = {"Survived", "NoCoverage"}       # CompileError / RuntimeError / Ignored count for neither


def is_js(path: str) -> bool:
    return path.lower().endswith(js_imports.JS_EXTS)


def default_test_path(workspace: str, target_file: str) -> str:
    """Where a generated Jest test sits: the project's own test dir if it has one, else beside
    the target. The name matches Jest's default testMatch, so the project config picks it up."""
    ext = "ts" if target_file.lower().endswith((".ts", ".tsx")) else "js"
    name = f"sentinel.generated.test.{ext}"
    for d in ("test", "tests", "__tests__"):
        if os.path.isdir(os.path.join(workspace, d)):
            return f"{d}/{name}"
    return posixpath.join(posixpath.dirname(target_file), name)


def import_path(test_path: str, target_file: str) -> str:
    """The require/import specifier a test at `test_path` uses to load `target_file`."""
    rel = posixpath.relpath(js_imports.module_key(target_file), posixpath.dirname(test_path) or ".")
    return rel if rel.startswith(".") else "./" + rel


def function_span(source: str, target_file: str, function: str) -> Optional[Tuple[int, int]]:
    """(line_start, line_end) of `function` (or `Class.function`). Parsed in a child process:
    tree-sitter leaks native state in the long-lived server (lib/ts_parse.py)."""
    defs = ts_parse.batch_isolated("defs", [[target_file, source]])[0]
    exact = [d for d in defs if d["name"] == function]
    tail = [d for d in defs if d["name"].rsplit(".", 1)[-1] == function.rsplit(".", 1)[-1]]
    hit = (exact or tail or [None])[0]
    return (hit["line_start"], hit["line_end"]) if hit else None


def ensure_tooling(run_id: Optional[str], workspace: str) -> Optional[str]:
    """Install Stryker (and, on a fresh clone, the repo's deps) once per workspace.
    Returns None when ready, else why not."""
    if os.path.isdir(os.path.join(workspace, "node_modules", "@stryker-mutator", "jest-runner")):
        return None
    npm = sandbox.which("npm")
    if not npm:
        return "npm is not available, so the test cannot be run"
    # ponytail: no per-repo cache dir of our own — sandboxd's per-repo dependency cache is the cache.
    try:
        r = sandbox.run(run_id, [npm, "install", "--no-save", "--ignore-scripts", "--no-audit",
                                 "--no-fund", *STRYKER_PKGS], workspace, INSTALL_TIMEOUT, network=True)
    except subprocess.TimeoutExpired:
        return f"installing Stryker exceeded {INSTALL_TIMEOUT}s"
    if r.returncode != 0:
        return "installing Stryker failed: " + ((r.stderr or r.stdout or "")[-300:]).strip()
    return None


def attach_node_modules(workspace: str, dst: str) -> None:
    """Make `workspace/node_modules` visible inside the copy at `dst` without copying it.
    Always pair with `detach_node_modules` before deleting `dst`."""
    src = os.path.join(workspace, "node_modules")
    link = os.path.join(dst, "node_modules")
    if not os.path.isdir(src) or os.path.lexists(link):
        return
    if sandbox.mode() == "docker":
        # A link made on the host points at a host path the container cannot see.
        # ponytail: a full copy per evaluation; mount the workspace's node_modules if it gets slow.
        shutil.copytree(src, link, symlinks=True)
    elif os.name == "nt":
        import _winapi                        # a junction needs no admin rights; a symlink does
        _winapi.CreateJunction(src, link)
    else:
        os.symlink(src, link, target_is_directory=True)


def detach_node_modules(dst: str) -> None:
    """Remove the link only. Deleting through it would empty the workspace's node_modules."""
    link = os.path.join(dst, "node_modules")
    if os.path.isjunction(link):
        os.rmdir(link)                        # removes the junction, never the target
    elif os.path.islink(link):
        os.unlink(link)


def run_jest(run_id: Optional[str], cwd: str, test_rel: str,
             timeout: int = JEST_TIMEOUT) -> Tuple[bool, str]:
    """(passed, tail) for one test file. A timeout or a missing jest counts as not passing."""
    npx = sandbox.which("npx")
    if not npx:
        return False, "npx is not available"
    cmd = [npx, "--no-install", "jest", "--ci", "--coverage=false", "--runTestsByPath", test_rel]
    try:
        r = sandbox.run(run_id, cmd, cwd, timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-400:]


def stryker(run_id: Optional[str], cwd: str, target_file: str, span: Tuple[int, int],
            test_rel: str, timeout: int = STRYKER_TIMEOUT) -> Dict[str, Any]:
    """Mutate ONLY `span` of `target_file`, run ONLY `test_rel` against each mutant.
    Returns {caught, missed, total} from Stryker's JSON report, or {error}."""
    npx = sandbox.which("npx")
    if not npx:
        return {"error": "npx is not available"}
    report = ".sentinel-mutation.json"            # relative: absolute host paths in a config
    conf = {                                      # file are not mapped into the sandbox
        "testRunner": "jest",
        "mutate": [f"{target_file}:{span[0]}-{span[1]}"],
        # ponytail: a repo whose jest config sets `testRegex` rejects this testMatch; rename the
        # override to testRegex for those if one turns up.
        "jest": {"enableFindRelatedTests": False,
                 "config": {"testMatch": [f"**/{posixpath.basename(test_rel)}"]}},
        "coverageAnalysis": "perTest",
        "reporters": ["json"], "jsonReporter": {"fileName": report},
        "concurrency": 2, "logLevel": "error", "fileLogLevel": "off",
        "cleanTempDir": "always", "allowConsoleColors": False,
    }
    with open(os.path.join(cwd, "stryker.sentinel.json"), "w", encoding="utf-8") as fh:
        json.dump(conf, fh)
    try:
        r = sandbox.run(run_id, [npx, "--no-install", "stryker", "run", "stryker.sentinel.json"],
                        cwd, timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"Stryker exceeded {timeout}s"}
    path = os.path.join(cwd, report)
    if not os.path.exists(path):
        return {"error": "Stryker produced no report: "
                         + ((r.stderr or r.stdout or "")[-300:]).strip()}
    with open(path, encoding="utf-8") as fh:
        return summarise_report(json.load(fh))


def summarise_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """Stryker's mutation-testing-report-schema JSON -> the caught/missed lists lib.evaluate uses."""
    caught: List[Dict[str, str]] = []
    missed: List[Dict[str, str]] = []
    for _path, f in sorted((data.get("files") or {}).items()):
        for m in f.get("mutants") or []:
            line = ((m.get("location") or {}).get("start") or {}).get("line")
            entry = {"id": f"stryker-{m.get('id')}", "operator": m.get("mutatorName", "?"),
                     "description": f"line {line}: `{(m.get('replacement') or '')[:80]}`"}
            if m.get("status") in CAUGHT:
                caught.append(entry)
            elif m.get("status") in MISSED:
                missed.append(entry)
    return {"caught": caught, "missed": missed, "total": len(caught) + len(missed)}


def demo() -> None:
    assert is_js("src/a.ts") and not is_js("a.py")
    assert TEST_RE.search("test/sentinel.generated.test.js") and not TEST_RE.search("test/x.js")
    ws = tempfile.mkdtemp(prefix="jsexec-demo-")
    try:
        assert default_test_path(ws, "src/catalog/pricing.js") == "src/catalog/sentinel.generated.test.js"
        os.makedirs(os.path.join(ws, "test"))
        assert default_test_path(ws, "src/x.ts") == "test/sentinel.generated.test.ts"
        assert import_path("test/sentinel.generated.test.js", "src/catalog/pricing.js") == "../src/catalog/pricing"
        assert import_path("src/sentinel.generated.test.js", "src/p.js") == "./p"

        src = "function a() {\n  return 1;\n}\nclass K {\n  m() { return 2; }\n}\n"
        assert function_span(src, "x.js", "a") == (1, 3)
        assert function_span(src, "x.js", "m") == (5, 5), "a bare method name finds Class.method"
        assert function_span(src, "x.js", "nope") is None

        # the link is removed without touching what it points at
        os.makedirs(os.path.join(ws, "node_modules", "pkg"))
        dst = tempfile.mkdtemp(prefix="jsexec-dst-")
        attach_node_modules(ws, dst)
        assert os.path.isdir(os.path.join(dst, "node_modules", "pkg"))
        detach_node_modules(dst)
        shutil.rmtree(dst)
        assert os.path.isdir(os.path.join(ws, "node_modules", "pkg")), "target must survive"
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    rep = {"files": {"src/p.js": {"mutants": [
        {"id": "1", "mutatorName": "EqualityOperator", "replacement": "a >= 1", "status": "Killed",
         "location": {"start": {"line": 3}}},
        {"id": "2", "mutatorName": "BlockStatement", "replacement": "{}", "status": "Survived",
         "location": {"start": {"line": 2}}},
        {"id": "3", "mutatorName": "StringLiteral", "replacement": '""', "status": "CompileError",
         "location": {"start": {"line": 4}}}]}}}
    s = summarise_report(rep)
    assert s["total"] == 2 and [m["id"] for m in s["caught"]] == ["stryker-1"], s
    assert s["missed"][0]["description"] == "line 2: `{}`", s
    print("lib/jsexec.py OK")


if __name__ == "__main__":
    demo()
