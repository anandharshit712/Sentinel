"""ts_parse (shared by ast_analyzer_tool, complexity_metrics_tool) — JS/TS parsing via tree-sitter.

Mirrors the shape the Python `ast`-based path already produces: `{name, kind, line_start,
line_end}` defs (qualified as `Class.method` when nested) and a branch-count complexity heuristic.
Approximate by design (same bar as the existing Python complexity heuristic): good enough for the
base-vs-head delta signal, not a spec-compliant CFG or full ECMAScript coverage.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from tree_sitter import Node, Query, QueryCursor, Tree
from tree_sitter_language_pack import get_language, get_parser

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


@lru_cache(maxsize=None)
def _query(grammar: str) -> Query:
    return Query(get_language(grammar), _DEF_QUERY + _FIELD_DEF[grammar])


def _parse(grammar: str, src: str) -> Tuple[Tree, bytes]:
    data = src.encode("utf-8", errors="replace")
    return get_parser(grammar).parse(data), data


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
    if not grammar or not src:
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
    if not grammar or not src:
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
    print("ts_parse: OK")


if __name__ == "__main__":
    demo()
