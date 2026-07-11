"""ts_parse (shared by ast_analyzer_tool, complexity_metrics_tool) — JS/TS parsing via tree-sitter.

Mirrors the shape the Python `ast`-based path already produces: `{name, kind, line_start,
line_end}` defs (qualified as `Class.method` when nested) and a branch-count complexity heuristic.
Approximate by design (same bar as the existing Python complexity heuristic): good enough for the
base-vs-head delta signal, not a spec-compliant CFG or full ECMAScript coverage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from tree_sitter import Node, Parser, Query, QueryCursor, Tree
from tree_sitter_language_pack import get_language

_EXT_GRAMMAR = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
                ".ts": "typescript", ".tsx": "tsx"}

# field_definition (js) vs public_field_definition (ts/tsx) differ in both node type name and the
# name-field's field name ("property" vs "name") — everything else is identical across grammars.
_FIELD_DEF = {
    "javascript": '(field_definition property: (property_identifier) @name value: [(arrow_function) (function_expression)]) @method',
    "typescript": '(public_field_definition name: (property_identifier) @name value: [(arrow_function) (function_expression)]) @method',
}
_FIELD_DEF["tsx"] = _FIELD_DEF["typescript"]

_DEF_QUERY = """
(function_declaration name: (identifier) @name) @func
(generator_function_declaration name: (identifier) @name) @func
(method_definition name: (property_identifier) @name) @method
(class_declaration name: (_) @name) @class
(variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)]) @func
"""

_BRANCH_TYPES = {"if_statement", "for_statement", "for_in_statement", "while_statement",
                  "do_statement", "catch_clause", "ternary_expression", "switch_case"}
_LOGICAL_OPS = {b"&&", b"||"}


def grammar_for_path(path: str) -> Optional[str]:
    dot = path.rfind(".")
    return _EXT_GRAMMAR.get(path[dot:].lower()) if dot != -1 else None


# tree-sitter parses in C and can STACK-OVERFLOW → SIGSEGV, taking the whole process down (not a
# catchable Python exception). Minified/bundled JS is the classic trigger: one enormous line with
# deeply-nested expressions (jquery.min.js, *-min.js). Skip oversized or long-line files before
# parsing — they're vendored/generated, never the code under review.
# ponytail: content guard, not sandboxing. If a NORMAL file ever segfaults, parse in a subprocess.
_MAX_SRC_BYTES = 500_000
_MAX_LINE_LEN = 5_000


def _unsafe_to_parse(src: str) -> bool:
    if len(src) > _MAX_SRC_BYTES:
        return True
    return any(len(line) > _MAX_LINE_LEN for line in src.splitlines())


def _query(grammar: str) -> Query:
    # NOT cached: a Query reused across many parsed trees accumulates native state and eventually
    # SEGFAULTs the process (same class of bug as reusing a Parser — see _parse). Build fresh.
    return Query(get_language(grammar), _DEF_QUERY + _FIELD_DEF[grammar])


def _parse(grammar: str, src: str) -> Tuple[Tree, bytes]:
    # Fresh Parser per call, NOT the cached tree_sitter_language_pack.get_parser() singleton:
    # reusing one Parser across many parses in a long-lived process accumulates native state and
    # eventually SEGFAULTs (exit 139), killing the whole neuro-san server. A repo with enough JS/TS
    # files crosses the threshold mid-audit. get_language() stays cached — grammars are immutable.
    data = src.encode("utf-8", errors="replace")
    return Parser(get_language(grammar)).parse(data), data


def _text(data: bytes, node: Node) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _matches(grammar: str, tree: Tree) -> List[Tuple[int, Dict[str, List[Node]]]]:
    return list(QueryCursor(_query(grammar)).matches(tree.root_node))


def _qualify(node: Node, name: str, class_names: Dict[int, str]) -> str:
    n = node.parent
    while n is not None:
        if n.id in class_names:
            return f"{class_names[n.id]}.{name}"
        n = n.parent
    return name


def extract_defs(path: str, src: str) -> List[Dict[str, Any]]:
    """[{"name", "kind" in (function|method|class), "line_start", "line_end"}] for one JS/TS file."""
    grammar = grammar_for_path(path)
    if not grammar or not src or _unsafe_to_parse(src):
        return []
    tree, data = _parse(grammar, src)
    matches = _matches(grammar, tree)

    class_names: Dict[int, str] = {}
    for _, caps in matches:
        if "class" in caps and "name" in caps:
            class_names[caps["class"][0].id] = _text(data, caps["name"][0])

    defs: List[Dict[str, Any]] = []
    for _, caps in matches:
        if "name" not in caps:
            continue
        name = _text(data, caps["name"][0])
        if "class" in caps:
            node, kind, qual = caps["class"][0], "class", name
        elif "method" in caps:
            node, kind = caps["method"][0], "method"
            qual = _qualify(node, name, class_names)
        elif "func" in caps:
            node, kind = caps["func"][0], "function"
            qual = _qualify(node, name, class_names)
        else:
            continue
        defs.append({"name": qual, "kind": kind,
                     "line_start": node.start_point.row + 1, "line_end": node.end_point.row + 1})
    return defs


def _count_branches(node: Node, data: bytes) -> int:
    count = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _BRANCH_TYPES:
            count += 1
        elif n.type == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and data[op.start_byte:op.end_byte] in _LOGICAL_OPS:
                count += 1
        stack.extend(n.children)
    return count


def complexity_and_length(path: str, src: str, qualified_name: str) -> Optional[Tuple[int, int]]:
    """(complexity, length) for the named function/method, or None if not found in `src`."""
    grammar = grammar_for_path(path)
    if not grammar or not src or _unsafe_to_parse(src):
        return None
    tree, data = _parse(grammar, src)
    matches = _matches(grammar, tree)

    class_names: Dict[int, str] = {}
    for _, caps in matches:
        if "class" in caps and "name" in caps:
            class_names[caps["class"][0].id] = _text(data, caps["name"][0])

    for _, caps in matches:
        if "name" not in caps:
            continue
        node = caps.get("method", caps.get("func"))
        if not node:
            continue
        node = node[0]
        name = _qualify(node, _text(data, caps["name"][0]), class_names)
        if name != qualified_name:
            continue
        length = node.end_point.row - node.start_point.row + 1
        return 1 + _count_branches(node, data), length
    return None


def file_metrics(path: str, src: str) -> Dict[str, List[int]]:
    """{qualified_name: [complexity, length]} for EVERY function/method in one parse.

    complexity_and_length re-parses the whole file per function; this parses once and returns all,
    so callers scanning many functions don't multiply the (leaky) tree-sitter work.
    """
    grammar = grammar_for_path(path)
    if not grammar or not src or _unsafe_to_parse(src):
        return {}
    tree, data = _parse(grammar, src)
    matches = _matches(grammar, tree)
    class_names: Dict[int, str] = {}
    for _, caps in matches:
        if "class" in caps and "name" in caps:
            class_names[caps["class"][0].id] = _text(data, caps["name"][0])
    out: Dict[str, List[int]] = {}
    for _, caps in matches:
        if "name" not in caps:
            continue
        node = caps.get("method", caps.get("func"))
        if not node:
            continue
        node = node[0]
        name = _qualify(node, _text(data, caps["name"][0]), class_names)
        length = node.end_point.row - node.start_point.row + 1
        out[name] = [1 + _count_branches(node, data), length]
    return out


# --- subprocess isolation -------------------------------------------------------------------------
# tree-sitter's native parse/query state leaks CUMULATIVELY: each call is fine, but enough of them
# in one long-lived process (the neuro-san server) eventually SEGFAULTs (exit 139), killing every
# in-flight run. A repo with many JS/TS files crosses the threshold mid-audit. Fresh Parser/Query
# only slow it. The robust fix: do a tool's whole batch in a SHORT-LIVED child that exits (freeing
# all native memory) before the leak matters. If the child still dies, the parent degrades to empty
# results instead of crashing.
import json as _json  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import sys as _sys  # noqa: E402

_BATCH = {"defs": lambda p, s: extract_defs(p, s), "metrics": lambda p, s: file_metrics(p, s)}


def _run_batch(mode: str, items: List[List[str]]) -> list:
    fn = _BATCH[mode]
    return [fn(path, src) for path, src in items]


def batch_isolated(mode: str, items: List[List[str]], chunk: int = 1) -> list:
    """Parse many [path, src] pairs in throwaway subprocess(es). mode: 'defs' | 'metrics'.
    Returns results aligned to `items`; on ANY failure (incl. a child segfault) that chunk degrades
    to empties — never propagates a crash to the caller.

    chunk=1 (one file per child) is deliberate: the leak crosses its SEGFAULT threshold well within a
    single 50-file batch, but a child parsing ONE file always survives. Empty-src items (e.g. the
    base side of an added file in audit mode) need no child at all.
    ponytail: chunk=1 spends ~1 process spawn per changed file. Raise it only with a measured safe
    ceiling — a chunk that crashes loses ALL its files' results, not just the offending one.
    """
    empty: Any = [] if mode == "defs" else {}
    results: list = [empty for _ in items]
    todo = [(i, it) for i, it in enumerate(items) if it and it[1]]  # skip empty sources
    for c in range(0, len(todo), chunk):
        group = todo[c:c + chunk]
        try:
            r = _subprocess.run([_sys.executable, "-m", "lib.ts_parse", "--worker"],
                                input=_json.dumps({"mode": mode, "items": [it for _, it in group]}),
                                capture_output=True, encoding="utf-8", errors="replace", timeout=180)
            if r.returncode == 0 and r.stdout.strip():
                for (idx, _), val in zip(group, _json.loads(r.stdout)):
                    results[idx] = val
        except Exception:
            pass  # this chunk degrades to empties; keep going
    return results


def demo() -> None:
    src = """
function foo(a) {
  if (a) { return 1; }
  return a && b || c;
}
class Bar {
  baz() { for (let i=0;i<1;i++) {} }
  qux = () => { switch(1) { case 1: break; } };
}
"""
    defs = extract_defs("bar.js", src)
    names = {d["name"] for d in defs}
    assert names == {"foo", "Bar", "Bar.baz", "Bar.qux"}, names
    cx, length = complexity_and_length("bar.js", src, "foo")
    assert cx == 4, cx  # base 1 + if + && + ||
    assert length == 4, length
    assert extract_defs("bar.ts", src.replace("function", "function"))  # ts grammar path too
    # segfault guard: minified/oversized input is skipped (returns empty), never fed to tree-sitter
    assert extract_defs("min.js", "var a=1;" * 2000) == []   # one 16k-char line (minified)
    assert extract_defs("big.js", "x = 1;\n" * 100_000) == []  # >500KB
    assert complexity_and_length("min.js", "a," * 4000, "f") is None
    fm = file_metrics("bar.js", src)
    assert fm["foo"] == [4, 4], fm  # same as complexity_and_length, one parse for all funcs
    # subprocess isolation: batch round-trips through a child process, aligned to input order
    b = batch_isolated("defs", [["bar.js", src], ["min.js", "x;" * 4000]])
    assert len(b) == 2 and {d["name"] for d in b[0]} == names and b[1] == [], b
    print("ts_parse: OK")


if __name__ == "__main__":
    if "--worker" in _sys.argv:  # subprocess batch worker (see batch_isolated)
        req = _json.loads(_sys.stdin.read())
        _sys.stdout.write(_json.dumps(_run_batch(req["mode"], req["items"])))
    else:
        demo()
