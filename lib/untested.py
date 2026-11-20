"""Which changed functions have no test at all — the rule that decides when CI writes one (Phase 6).

Test generation in CI is only worth a model call, and a reviewer's attention, for code nothing
tests. So the decision is a rule, not an opinion (rule 4): a changed function is a **candidate**
only if all three of these are false —

  (a) coverage shows any line of its BODY executed (the `def` line runs at import, so it never
      counts — same body-only rule as coverage_gap). Partly covered is covered: some test reaches
      it, and that test is the place to extend, not a new generated file.
  (b) an existing test file imports its module AND references its name (Python: `ast`; JS/TS:
      relative imports via lib.js_imports, identifiers via tree-sitter — so a mention in a comment
      or a string does not count).
  (c) the PR itself adds or modifies a test that references it — the author already wrote one.

No candidates -> no model call and nothing posted. At most `max_generated_per_pr` (repo_config,
default 3) per PR, highest priority first, so a 40-function refactor cannot start 40 generations.

`candidates_for_run` is the Gateway's entry point after a CI run's decision is persisted;
`generate` runs `sentinel_testgen` once per candidate through a caller-supplied `invoke`, so this
module never imports the Gateway and the verify script exercises the same code path.

    python -m lib.untested        # self-check
"""
from __future__ import annotations

import ast
import logging
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

import yaml

from lib import js_imports, jsexec, ts_parse

logger = logging.getLogger("lib.untested")

DEFAULT_CAP = 3
PROMPT = "Write a test for the highest-priority coverage gap in this run."
_REPO_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "repo_config.yaml")
_PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build",
          ".pytest_cache", ".stryker-tmp", "coverage", "site-packages"}
_MAX_TEST_BYTES = 300_000


# ---- what is a test, and what does it reference ----------------------------------------------

def is_test_file(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    if base.endswith(".py"):
        return base.startswith("test_") or base.endswith("_test.py")
    return bool(jsexec.TEST_RE.search(base)) or ("/__tests__/" in f"/{rel}" and jsexec.is_js(rel))


def _py_modules(path: str) -> Set[str]:
    """Every dotted name a test could import `path` by: `src/pkg/shop.py` -> src.pkg.shop,
    pkg.shop, shop (src-layouts and sys.path tweaks both import by a suffix)."""
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return {".".join(parts[i:]) for i in range(len(parts)) if parts[i:]}


def _py_refs(src: str) -> Optional[Dict[str, Set[str]]]:
    """{'imports': dotted modules imported, 'names': identifiers used}. None when unparseable."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    imports: Set[str] = set()
    names: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            imports.add(n.module)
            imports.update(f"{n.module}.{a.name}" for a in n.names)
            # `from shop import f as g; g()` — g is how the test refers to f
            names.update(a.name for a in n.names if a.asname and _used(tree, a.asname))
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return {"imports": imports, "names": names}


def _used(tree: ast.AST, ident: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == ident for n in ast.walk(tree))


def _js_names(items: List[List[str]]) -> List[Set[str]]:
    """Identifiers per [path, src], parsed in a child process (tree-sitter leaks in long-lived ones)."""
    return [set(r) for r in ts_parse.batch_isolated("idents", items)] if items else []


def _short(function: str) -> str:
    return function.rsplit(".", 1)[-1]


# ---- the rule ---------------------------------------------------------------------------------

def _changed_functions(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for f in profile.get("files", []) or []:
        path = f.get("path", "")
        if f.get("change_type") == "deleted" or is_test_file(path):
            continue
        if not (path.endswith(".py") or jsexec.is_js(path)):
            continue
        for fn in f.get("functions_changed", []) or []:
            if fn.get("kind", "function") in ("function", "method") and fn.get("line_start"):
                out.append({**fn, "file": path})
    return out


def _read_tests(workspace: str, only: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """{rel_path: source} for every test file in the workspace (or just `only`)."""
    rels: List[str] = []
    if only is not None:
        rels = [r for r in only if is_test_file(r)]
    else:
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in _PRUNE and not d.startswith(".")]
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), workspace).replace(os.sep, "/")
                if is_test_file(rel):
                    rels.append(rel)
    out: Dict[str, str] = {}
    for rel in rels:
        p = os.path.join(workspace, rel.replace("/", os.sep))
        try:
            if os.path.getsize(p) <= _MAX_TEST_BYTES:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    out[rel] = fh.read()
        except OSError:
            continue
    return out


def _referencing(fn: Dict[str, Any], tests: Dict[str, str], need_import: bool,
                 js_idents: Dict[str, Set[str]]) -> Optional[str]:
    """The first test file that references `fn` (and, with need_import, imports its module)."""
    path, name = fn["file"], _short(fn["name"])
    word = re.compile(rf"\b{re.escape(name)}\b")
    if path.endswith(".py"):
        mods = _py_modules(path)
        for rel, src in sorted(tests.items()):
            if not rel.endswith(".py") or not word.search(src):
                continue
            refs = _py_refs(src)
            if not refs or name not in refs["names"]:
                continue
            if not need_import or refs["imports"] & mods:
                return rel
        return None
    key = js_imports.module_key(path)
    for rel, src in sorted(tests.items()):
        if not jsexec.is_js(rel) or not word.search(src):
            continue
        if need_import and key not in js_imports.extract_relative_targets(src, js_imports.module_key(rel)):
            continue
        if name in js_idents.get(rel, set()):
            return rel
    return None


def candidates(profile: Dict[str, Any], coverage_gaps: Optional[Dict[str, Any]], workspace: str,
               cap: int = DEFAULT_CAP) -> Dict[str, Any]:
    """Apply the rule. Returns {candidates, skipped, truncated, measured, cap}.

    `candidates` are coverage_gaps-shaped, so gap_context reads them exactly like a gap.
    `skipped` names the condition that excluded each function — the reason nothing was generated.
    """
    cov = coverage_gaps or {}
    by_fn = {(g.get("file"), g.get("function")): g for g in cov.get("gaps") or []}
    sensitive: Set[str] = set()
    for sf in profile.get("sensitive_flags", []) or []:
        sensitive.update(sf.get("files", []) or [])
    changed_tests = {f["path"] for f in profile.get("files", []) or []
                     if f.get("change_type") != "deleted" and is_test_file(f.get("path", ""))}

    fns = _changed_functions(profile)
    tests = _read_tests(workspace) if fns else {}
    for rel, src in _read_tests(workspace, changed_tests).items():
        tests.setdefault(rel, src)
    # tree-sitter only for JS test files that textually mention a changed JS function's name
    js_names = {_short(f["name"]) for f in fns if jsexec.is_js(f["file"])}
    js_items = [[rel, src] for rel, src in sorted(tests.items()) if jsexec.is_js(rel)
                and any(re.search(rf"\b{re.escape(n)}\b", src) for n in js_names)]
    js_idents = dict(zip([i[0] for i in js_items], _js_names(js_items)))

    picked: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for fn in fns:
        g = by_fn.get((fn["file"], fn["name"])) or {}
        status = g.get("status") or "unmeasured"
        base = {"file": fn["file"], "function": fn["name"]}
        if status in ("covered", "partial"):                                       # (a)
            skipped.append({**base, "reason": f"coverage: body executed ({status})"})
            continue
        pr_test = _referencing(fn, {r: s for r, s in tests.items() if r in changed_tests},
                               need_import=False, js_idents=js_idents)
        if pr_test:                                                                # (c)
            skipped.append({**base, "reason": f"this PR adds or changes a test for it: {pr_test}"})
            continue
        existing = _referencing(fn, {r: s for r, s in tests.items() if r not in changed_tests},
                                need_import=True, js_idents=js_idents)
        if existing:                                                               # (b)
            skipped.append({**base, "reason": f"an existing test imports and calls it: {existing}"})
            continue
        is_new, sens = bool(fn.get("is_new")), fn["file"] in sensitive
        span = list(range(int(fn["line_start"]) + 1, int(fn.get("line_end") or fn["line_start"]) + 1))
        picked.append({
            "file": fn["file"], "function": fn["name"],
            "line_start": fn["line_start"], "line_end": fn.get("line_end") or fn["line_start"],
            "status": "uncovered" if status == "uncovered" else "unmeasured",
            "uncovered_lines": g.get("uncovered_lines") or (span if status == "uncovered" else []),
            "is_new": is_new, "sensitive": sens,
            "language": "javascript" if jsexec.is_js(fn["file"]) else "python",
            # sensitive beats new beats size — the same order coverage_gap ranks by
            "priority": g.get("priority") or (2.0 + (4.0 if sens else 0) + (2.0 if is_new else 0)
                                              + min(len(span), 20) / 10.0),
        })
    picked.sort(key=lambda c: (-c["priority"], c["file"], c["line_start"]))
    cap = max(0, int(cap))
    return {"candidates": picked[:cap], "truncated": max(0, len(picked) - cap),
            "skipped": skipped, "measured": bool(cov.get("measured")), "cap": cap}


def cap_for(repo_name: Optional[str], config_path: str = _REPO_CONFIG) -> int:
    """`max_generated_per_pr` for the repo, else the defaults, else 3."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except OSError:
        return DEFAULT_CAP
    repo = ((cfg.get("repos") or {}).get(repo_name) or {}) if repo_name else {}
    for src in (repo, cfg.get("defaults") or {}):
        if "max_generated_per_pr" in src:
            return int(src["max_generated_per_pr"])
    return DEFAULT_CAP


def candidates_for_run(sly: Dict[str, Any], workspace: str,
                       repo_name: Optional[str] = None) -> Dict[str, Any]:
    """The Gateway hook's half that needs the gate run's workspace: call it after the decision is
    persisted, while the workspace still exists. Pure reads; never raises.

    `sly` is the sly_data the promotion network returned (change_profile + coverage_gaps are on
    its allow-list; the event is not, so pass `repo_name` for the per-repo cap)."""
    try:
        repo_name = repo_name or ((sly.get("event") or {}).get("repo") or {}).get("name")
        return candidates(sly.get("change_profile") or {}, sly.get("coverage_gaps"), workspace,
                          cap_for(repo_name))
    except Exception as e:                       # advisory: a bug here must never touch the run
        logger.warning("untested: candidate selection failed: %s", e)
        return {"candidates": [], "truncated": 0, "skipped": [], "measured": False,
                "cap": 0, "error": str(e)[:300]}


def generate(selection: Dict[str, Any], invoke: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
    """Run sentinel_testgen once per candidate. `invoke(extra_sly)` runs the network with that
    sly_data on top of the run's own (the Gateway's invoke_network, or a verify script's session).

    Zero candidates returns at once: no model call. One retry per candidate — generation is
    advisory and the provider throws intermittent 500s; a gate would never get a retry.
    """
    cands = selection.get("candidates") or []
    done: List[Dict[str, Any]] = []
    for c in cands:
        extra = {"coverage_gaps": {"measured": selection.get("measured", False), "gaps": [c]}}
        error = None
        for _attempt in (1, 2):
            try:
                invoke(extra)
                error = None
                break
            except Exception as e:
                error = str(e)[:300]
                logger.warning("untested: generation for %s::%s failed: %s", c["file"], c["function"], error)
        done.append({"file": c["file"], "function": c["function"], "error": error})
    return {"attempted": len(done), "results": done, "truncated": selection.get("truncated", 0)}


def demo() -> None:
    import shutil
    import tempfile

    ws = tempfile.mkdtemp(prefix="untested-demo-")

    def w(rel: str, body: str) -> None:
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)

    try:
        w("shop.py", "def tested(x):\n    return x\n\n\ndef bare(x):\n    return x * 2\n")
        w("tests/test_shop.py", "from shop import tested\n\n\ndef test_t():\n    assert tested(1) == 1\n")
        w("src/p.js", "function used(a) {\n  return a;\n}\nfunction lonely(a) {\n  return a + 1;\n}\n"
                      "module.exports = { used, lonely };\n")
        w("test/p.test.js", "const { used } = require('../src/p');\n// lonely is not tested\n"
                            "test('u', () => expect(used(1)).toBe(1));\n")
        profile = {"files": [
            {"path": "shop.py", "change_type": "modified", "functions_changed": [
                {"name": "tested", "kind": "function", "line_start": 1, "line_end": 2},
                {"name": "bare", "kind": "function", "line_start": 5, "line_end": 6, "is_new": True}]},
            {"path": "src/p.js", "change_type": "modified", "functions_changed": [
                {"name": "used", "kind": "function", "line_start": 1, "line_end": 3},
                {"name": "lonely", "kind": "function", "line_start": 4, "line_end": 6}]}]}
        out = candidates(profile, {"measured": False, "gaps": []}, ws)
        names = [c["function"] for c in out["candidates"]]
        assert names == ["bare", "lonely"], out          # a comment mentioning `lonely` is not a test
        assert {s["function"] for s in out["skipped"]} == {"tested", "used"}, out["skipped"]

        calls: List[Dict[str, Any]] = []
        assert generate({"candidates": []}, calls.append)["attempted"] == 0 and calls == []
        assert generate(candidates(profile, {}, ws, cap=1), calls.append)["attempted"] == 1
        assert calls[0]["coverage_gaps"]["gaps"][0]["function"] == "bare", "highest priority first"
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("lib/untested.py OK")


if __name__ == "__main__":
    demo()
