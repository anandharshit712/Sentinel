"""Deterministic AST mutation operators (09 §4 P3.5).

The evidence behind "this generated test is worth showing a human". A test that passes proves
nothing on its own — it may assert that `add(1, 2) == 3` without ever exercising the branch it was
written for, or assert nothing at all. So: break the function on purpose, in small realistic ways,
and see whether the test notices. Caught mutants are the measurement; everything else in the
evaluation is bookkeeping.

Operators are the classic set, chosen because they model bugs people actually write:

    comparison     <  ->  <=      an off-by-one in a boundary check
    boundary       n  ->  n ± 1   the same bug in the literal
    logic          and <-> or     a mishandled condition
    negation       return x -> return not x
    arithmetic     +  ->  -       a sign slip
    statement      drop a statement (an early return, a guard, a mutation of state)

Scope is one function at a time — the changed function a test was generated for — so a mutation
campaign costs a handful of test runs, not a full-suite sweep per mutant.

Determinism matters: same source in, same mutants out, in the same order, so a mutation score is
reproducible and comparable across runs.

    python -m lib.mutate        # self-check
"""
from __future__ import annotations

import ast
import logging
from typing import Any, List, NamedTuple

logger = logging.getLogger("lib.mutate")

MAX_MUTANTS = 20  # a campaign is a handful of test runs, not an afternoon


class Mutant(NamedTuple):
    id: str
    operator: str
    description: str   # human-readable, shown next to a caught/missed verdict
    line: int
    source: str        # the full mutated module source


_CMP_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.Div: ast.Mult}
_CMP_NAME = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
}
_BIN_NAME = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//"}


def _in_target(node: ast.AST, target: ast.AST) -> bool:
    return getattr(node, "lineno", -1) >= getattr(target, "lineno", 0) and \
        getattr(node, "end_lineno", -1) <= getattr(target, "end_lineno", 10 ** 9)


def find_function(tree: ast.AST, name: str) -> ast.AST | None:
    """The def/class node called `name`, including methods (`Class.method` or bare `method`)."""
    want = name.rsplit(".", 1)[-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == want:
            return node
    return None


class _Mutator(ast.NodeTransformer):
    """Applies exactly ONE edit — the `nth` opportunity of `kind` — so each mutant isolates one bug."""

    def __init__(self, kind: str, nth: int, target: ast.AST):
        self.kind, self.nth, self.target = kind, nth, target
        self.seen = 0
        self.applied: tuple[int, str] | None = None   # (line, description)

    def _take(self, node: ast.AST) -> bool:
        if not _in_target(node, self.target):
            return False
        hit = self.seen == self.nth
        self.seen += 1
        return hit

    def visit_Compare(self, node: ast.Compare) -> Any:
        self.generic_visit(node)
        if self.kind == "comparison" and len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
            if self._take(node):
                old = type(node.ops[0])
                new = _CMP_SWAP[old]
                node.ops = [new()]
                self.applied = (node.lineno, f"`{_CMP_NAME[old]}` -> `{_CMP_NAME[new]}`")
        return node

    def visit_Constant(self, node: ast.Constant) -> Any:
        if self.kind == "boundary" and isinstance(node.value, int) and not isinstance(node.value, bool):
            if self._take(node):
                self.applied = (node.lineno, f"`{node.value}` -> `{node.value + 1}`")
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        self.generic_visit(node)
        if self.kind == "logic":
            if self._take(node):
                is_and = isinstance(node.op, ast.And)
                node.op = ast.Or() if is_and else ast.And()
                self.applied = (node.lineno, f"`{'and' if is_and else 'or'}` -> "
                                             f"`{'or' if is_and else 'and'}`")
        return node

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        self.generic_visit(node)
        if self.kind == "arithmetic" and type(node.op) in _BIN_SWAP:
            if self._take(node):
                old = type(node.op)
                new = _BIN_SWAP[old]
                node.op = new()
                self.applied = (node.lineno, f"`{_BIN_NAME[old]}` -> `{_BIN_NAME[new]}`")
        return node

    def visit_Return(self, node: ast.Return) -> Any:
        self.generic_visit(node)
        if self.kind == "negation" and node.value is not None:
            if self._take(node):
                self.applied = (node.lineno, "negated the returned value")
                return ast.copy_location(
                    ast.Return(value=ast.UnaryOp(op=ast.Not(), operand=node.value)), node)
        return node

    def visit_Expr(self, node: ast.Expr) -> Any:
        # Drop a bare expression statement (a mutation of state, a logged side effect, a call whose
        # result is discarded). Docstrings are left alone — removing one changes nothing.
        if self.kind == "statement" and not (isinstance(node.value, ast.Constant)
                                             and isinstance(node.value.value, str)):
            if self._take(node):
                self.applied = (node.lineno, "removed a statement")
                return ast.copy_location(ast.Pass(), node)
        return node


_KINDS = ("comparison", "boundary", "logic", "negation", "arithmetic", "statement")


def _count(kind: str, source: str, function_name: str) -> int:
    """How many opportunities of `kind` exist inside the target function.

    Parses `source` fresh and locates the target in THAT tree: line numbers are the only link
    between a node and its enclosing function, and they shift the moment a tree is unparsed.
    """
    tree = ast.parse(source)
    target = find_function(tree, function_name)
    if target is None:
        return 0
    m = _Mutator(kind, -1, target)   # -1 never matches: this pass only counts opportunities
    m.visit(tree)
    return m.seen


def generate(source: str, function_name: str, limit: int = MAX_MUTANTS) -> List[Mutant]:
    """Mutants of one function, in a stable order. Invalid source or unknown function -> []."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logger.info("mutate: source does not parse: %s", e)
        return []
    target = find_function(tree, function_name)
    if target is None:
        logger.info("mutate: no function named %r", function_name)
        return []

    out: List[Mutant] = []
    for kind in _KINDS:                       # fixed order -> reproducible mutant ids
        total = _count(kind, source, function_name)
        for nth in range(total):
            if len(out) >= limit:
                return out
            fresh = ast.parse(source)
            tgt = find_function(fresh, function_name)
            mut = _Mutator(kind, nth, tgt)
            new_tree = mut.visit(fresh)
            if not mut.applied:
                continue
            ast.fix_missing_locations(new_tree)
            try:
                mutated = ast.unparse(new_tree)
            except Exception as e:           # pragma: no cover - unparse is total in practice
                logger.info("mutate: could not unparse %s#%d: %s", kind, nth, e)
                continue
            if mutated.strip() == ast.unparse(ast.parse(source)).strip():
                continue                      # a no-op edit is not a mutant
            line, desc = mut.applied
            out.append(Mutant(id=f"{kind}-{nth + 1}", operator=kind,
                              description=f"line {line}: {desc}", line=line, source=mutated))
    return out


def demo() -> None:
    src = (
        "def price(qty, unit, member):\n"
        "    if qty > 10 and member:\n"
        "        return qty * unit * 0.9\n"
        "    return qty * unit\n"
    )
    muts = generate(src, "price")
    kinds = {m.operator for m in muts}
    assert {"comparison", "boundary", "logic", "arithmetic"} <= kinds, kinds
    assert all(m.source != src for m in muts), "a mutant must differ from the original"
    for m in muts:
        compile(m.source, "<mutant>", "exec")          # every mutant must be runnable

    # each mutant isolates exactly ONE edit
    one = next(m for m in muts if m.operator == "comparison")
    assert one.source.count(">=") == 1 and ">" in one.source
    assert "or" not in one.source, "the comparison mutant must not also flip the boolean operator"

    # determinism: same input, same mutants, same order
    assert [m.id for m in generate(src, "price")] == [m.id for m in muts]
    assert [m.source for m in generate(src, "price")] == [m.source for m in muts]

    # the mutants really do change behaviour
    ns: dict = {}
    exec(compile(src, "<orig>", "exec"), ns)
    orig = ns["price"](11, 10.0, True)                  # 99.0
    changed = 0
    for m in muts:
        mns: dict = {}
        try:
            exec(compile(m.source, "<m>", "exec"), mns)
            if mns["price"](11, 10.0, True) != orig:
                changed += 1
        except Exception:
            changed += 1                                # a crash is a detectable difference too
    assert changed >= len(muts) // 2, f"only {changed}/{len(muts)} mutants altered behaviour"

    # negation + statement operators, and methods by bare name
    cls = ("class Cart:\n"
           "    def ok(self):\n"
           "        self.log('x')\n"
           "        return self.valid\n")
    cm = generate(cls, "ok")
    assert {"negation", "statement"} <= {m.operator for m in cm}, [m.operator for m in cm]
    assert any("not self.valid" in m.source for m in cm)

    # docstrings are not "statements" worth dropping
    doc = "def f():\n    'a docstring'\n    return 1\n"
    assert not any(m.operator == "statement" for m in generate(doc, "f"))

    # bad input is empty, not an exception
    assert generate("def broken(:\n", "broken") == []
    assert generate(src, "nonexistent") == []
    assert len(generate(src, "price", limit=2)) == 2

    print("lib/mutate.py OK")


if __name__ == "__main__":
    demo()
