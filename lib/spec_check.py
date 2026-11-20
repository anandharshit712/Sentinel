"""Deterministic specification checks: does the code do what its docstring says? (11 §3, Tier 3)

The blind oracle (Tier 2) is the general answer to "is the expectation right", and it needs a
model. This module takes the commonest instance of that question and answers it with arithmetic:

    \"\"\"Members of 3+ years get 15% off.\"\"\"     # the claim
    return round(total * 0.90, 2)                 # the code applies 10%

A docstring that states a rate implies a multiplier — 15% off implies 0.85, a 15% fee implies 1.15.
If the function multiplies by a constant and that constant matches neither, the code and its
documentation disagree, and a test generated from the code will silently take the code's side.

Three more invariants follow the same shape (12 §8): a claim the author wrote down, checked
against what the code visibly does on its literal paths.

    bounds             "Returns a score between 0 and 100."   ...  return -1
    idempotence        "Idempotent: safe to call twice."      ...  self.items.append(x)
    type preservation  `-> str`                               ...  return 0

Each was kept only after firing on zero correct functions in the `humanize` trial set
(docs/writeup/real-repo-trials.md §2); `scripts/spec_check_fp.py` reproduces the count.

Static only: it reads literals, it never executes anything. That keeps it usable on code nobody has
vetted, and it means the check costs nothing and cannot flake. A value the code computes is never
judged — only literals on a return path or an unconditional write.

**Every finding here is advisory.** A mismatch means the code and the docs disagree, not which one
is wrong — the docstring may simply be stale. That distinction belongs to a human, so the output
names both sides and never picks one.

    python -m lib.spec_check        # self-check
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from lib.intent import documented_numbers

TOLERANCE = 0.005          # 0.85 vs 0.849 is the same claim; 0.85 vs 0.90 is not
MIN_MULTIPLIER = 0.01      # ignore unit conversions and tiny scaling factors
MAX_MULTIPLIER = 100.0

# Which side is wrong is not knowable from here, and pretending otherwise would be the same
# overclaim this whole document exists to remove.
CAVEAT = ("This says the code and its documentation disagree, not which one is right — the "
          "documentation may be stale. A test generated from the code will assert the code's "
          "behaviour either way.")


def check_function(source: str, function: str) -> List[Dict[str, Any]]:
    """Every mismatch between what `function` documents and what its code does."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return check_node(node)
    return []


def check_node(node: ast.AST) -> List[Dict[str, Any]]:
    """`check_function` for an already-parsed def (every method of a class, not just the first)."""
    doc = ast.get_docstring(node) or ""
    return (_rate_mismatch(node, doc) + _bound_violation(node, doc)
            + _idempotence_violation(node, doc) + _return_type_mismatch(node, doc))


def _own_nodes(fn: ast.AST) -> Iterator[Tuple[ast.AST, bool]]:
    """(node, under_an_if) for `fn`'s own body — not nested functions, lambdas or classes, whose
    returns and side effects belong to them."""
    stack = [(n, False) for n in reversed(fn.body)]
    while stack:
        node, guarded = stack.pop()
        yield node, guarded
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        inner = guarded or isinstance(node, (ast.If, ast.IfExp))
        stack.extend((c, inner) for c in reversed(list(ast.iter_child_nodes(node))))


# ---- documented rate ----------------------------------------------------------------------------
def _scaling_literals(node: ast.AST) -> List[float]:
    """Numeric constants the function scales by — the operands of `*` and `/`."""
    out: List[float] = []
    for n in ast.walk(node):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div)):
            for side in (n.left, n.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, (int, float)) \
                        and not isinstance(side.value, bool):
                    v = float(side.value)
                    if MIN_MULTIPLIER <= abs(v) <= MAX_MULTIPLIER:
                        out.append(round(v, 6))
    return out


def _rate_mismatch(target: ast.AST, doc: str) -> List[Dict[str, Any]]:
    function = target.name
    claims = documented_numbers(doc)
    if not claims["percentages"]:
        return []                       # nothing was claimed, so nothing can disagree

    literals = _scaling_literals(target)
    if not literals:
        return []                       # the rate may be computed elsewhere; silence beats a guess

    expected = claims["multipliers"]
    # A rate can also appear directly (total * 0.15 to compute the discount amount itself).
    expected_direct = [round(p / 100, 4) for p in claims["percentages"]]
    allowed = expected + expected_direct

    matched = [lit for lit in literals
               if any(abs(lit - e) <= TOLERANCE for e in allowed)]
    if matched:
        return []                       # the code applies a rate the docstring states

    return [{
        "kind": "documented_rate_mismatch",
        "function": function,
        "documented_percentages": claims["percentages"],
        "implied_multipliers": allowed,
        "code_multiplies_by": sorted(set(literals)),
        "evidence": (f"the docstring states {claims['percentages'][0]:g}%, which implies a "
                     f"multiplier of {allowed[0]:g}, but {function} scales by "
                     f"{', '.join(str(x) for x in sorted(set(literals)))}"),
        "caveat": CAVEAT,
        "confidence": "moderate",
    }]


# ---- bounds -------------------------------------------------------------------------------------
# "Returns a value between 0 and 1", "the result is in the range [0, 100]", "returns a non-negative
# int". Only claims about the RETURN value: "for numbers 0-9" describes the input domain.
_NUM = r"(-?\d+(?:\.\d+)?)"
_BOUND = re.compile(r"(?is)\b(?:returns?|returned|result|output)\b[^.;]{0,80}?(?:"
                    r"\bbetween\s+" + _NUM + r"\s+and\s+" + _NUM + r"|"
                    r"\bin\s+(?:the\s+)?(?:range|interval)\s*[\[(]\s*" + _NUM + r"\s*,\s*" + _NUM
                    + r"\s*[\])]|"
                    r"\b(non-?negative|never\s+negative))")


def _number(node: Optional[ast.AST]) -> Optional[float]:
    """A numeric literal (`3`, `-1`, `0.5`), else None. Booleans are not numbers here."""
    sign = 1.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        sign, node = -1.0, node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return sign * float(node.value)
    return None


def _mentioned(doc: str, value: float) -> bool:
    """The docstring names this value itself — a documented exception ("or -1 if not found")."""
    return any(abs(float(m) - value) < 1e-9 for m in re.findall(r"-?\d+(?:\.\d+)?", doc))


def _bound_violation(target: ast.AST, doc: str) -> List[Dict[str, Any]]:
    m = _BOUND.search(doc)
    if not m:
        return []
    if m.group(5):
        lo, hi = 0.0, float("inf")
    else:
        lo, hi = sorted(float(x) for x in (m.group(1) or m.group(3), m.group(2) or m.group(4)))
    bad = sorted({v for n, _g in _own_nodes(target) if isinstance(n, ast.Return)
                  for v in [_number(n.value)]
                  if v is not None and not lo <= v <= hi and not _mentioned(doc, v)})
    if not bad:
        return []
    claim = " ".join(m.group(0).split())
    return [{
        "kind": "documented_bound_violation",
        "function": target.name,
        "documented_bounds": [lo, hi],
        "code_returns": bad,
        "evidence": (f"the docstring says \"{claim[:120]}\", but {target.name} returns "
                     f"{', '.join(f'{v:g}' for v in bad)} on one of its paths"),
        "caveat": CAVEAT,
        "confidence": "moderate",
    }]


# ---- idempotence --------------------------------------------------------------------------------
_IDEMPOTENT = re.compile(r"(?i)\bidempotent\b|\bsafe\s+to\s+call\s+(?:more\s+than\s+once|"
                         r"repeatedly|multiple\s+times|twice|again)|\bcalling\s+it\s+(?:again|twice)"
                         r"\s+has\s+no\s+(?:further\s+|additional\s+)?effect")
_ACCUMULATE = {"append", "extend", "insert", "appendleft", "extendleft"}


def _local_names(fn: ast.AST) -> Set[str]:
    """Names created inside `fn` (not parameters, not globals): writes through them do not escape."""
    outer = {n for node, _g in _own_nodes(fn) if isinstance(node, (ast.Global, ast.Nonlocal))
             for n in node.names}
    bound = {node.id for node, _g in _own_nodes(fn)
             if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    return bound - outer


def _escapes(node: ast.AST, local: Set[str]) -> bool:
    """Does writing through `node` reach state that outlives the call (self.x, a parameter, a global)?"""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return not (isinstance(node, ast.Name) and node.id in local)


def _idempotence_violation(target: ast.AST, doc: str) -> List[Dict[str, Any]]:
    """An idempotence claim over code that accumulates outside state with no guard.

    Only unconditional writes count: `if x not in self.seen: self.seen.append(x)` is how an
    idempotent registration is written, so anything under an `if` gets the benefit of the doubt.
    """
    if not _IDEMPOTENT.search(doc):
        return []
    local = _local_names(target)
    hits: List[str] = []
    for node, guarded in _own_nodes(target):
        if guarded:
            continue
        if isinstance(node, ast.AugAssign) and _escapes(node.target, local):
            hits.append(ast.unparse(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _ACCUMULATE and _escapes(node.func.value, local):
            hits.append(ast.unparse(node))
    if not hits:
        return []
    return [{
        "kind": "documented_idempotence_violation",
        "function": target.name,
        "code_accumulates": hits[:3],
        "evidence": (f"the docstring says {target.name} is idempotent, but every call "
                     f"unconditionally runs `{hits[0][:80]}`, so a second call changes state again"),
        "caveat": CAVEAT,
        "confidence": "moderate",
    }]


# ---- type preservation --------------------------------------------------------------------------
_TYPE_NAMES = {"str", "int", "float", "bool", "bytes", "list", "dict", "tuple", "set", "frozenset",
               "complex"}
_TYPING_ALIAS = {"List": "list", "Dict": "dict", "Tuple": "tuple", "Set": "set",
                 "FrozenSet": "frozenset", "Text": "str"}
_LITERAL = {ast.JoinedStr: "str", ast.List: "list", ast.ListComp: "list", ast.Dict: "dict",
            ast.DictComp: "dict", ast.Tuple: "tuple", ast.Set: "set", ast.SetComp: "set"}
# PEP 484 numeric tower: an int is an acceptable float, a bool an acceptable int.
_ACCEPTS = {"float": {"int", "bool"}, "complex": {"int", "float", "bool"}, "int": {"bool"}}


def _declared(ann: Optional[ast.AST]) -> Optional[Set[str]]:
    """Builtin type names an annotation allows, or None when it names anything this check cannot
    reason about (a class, a TypeVar, Any, a protocol) — then the check stays silent."""
    if ann is None:
        return None
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):      # "str | None"
        try:
            return _declared(ast.parse(ann.value, mode="eval").body)
        except SyntaxError:
            return None
    if isinstance(ann, ast.Constant) and ann.value is None:
        return {"None"}
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        a, b = _declared(ann.left), _declared(ann.right)
        return a | b if a is not None and b is not None else None
    node = ann.value if isinstance(ann, ast.Subscript) else ann
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    if isinstance(ann, ast.Subscript) and name in ("Optional", "Union"):
        args = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
        parts = [_declared(a) for a in args]
        if any(p is None for p in parts):
            return None
        out = set().union(*parts)
        return out | {"None"} if name == "Optional" else out
    if name == "None":
        return {"None"}
    name = _TYPING_ALIAS.get(name, name)
    return {name} if name in _TYPE_NAMES else None


def _literal_type(value: Optional[ast.AST]) -> Optional[str]:
    if value is None:
        return "None"                                   # a bare `return`
    if isinstance(value, ast.Constant):
        return "None" if value.value is None else type(value.value).__name__
    return _LITERAL.get(type(value))


def _return_type_mismatch(target: ast.AST, doc: str) -> List[Dict[str, Any]]:
    allowed = _declared(target.returns)
    if allowed is None:
        return []
    own = list(_own_nodes(target))
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n, _g in own):
        return []                                       # the annotation describes a generator
    bad: List[Tuple[str, str]] = []
    for node, _g in own:
        if not isinstance(node, ast.Return):
            continue
        t = _literal_type(node.value)
        if t is None or t in allowed or any(t in _ACCEPTS.get(a, ()) for a in allowed):
            continue
        # The docstring shows this value itself ("clamp(None) is None"): the docs already
        # document the return; only the annotation is behind. Same rule as bounds' _mentioned.
        if t == "None" and re.search(r"\bNone\b", doc):
            continue
        bad.append((t, ast.unparse(node)))
    if not bad:
        return []
    declared = " | ".join(sorted(allowed))
    return [{
        "kind": "return_type_mismatch",
        "function": target.name,
        "declared_return": declared,
        "code_returns": sorted({t for t, _s in bad}),
        "evidence": (f"{target.name} is annotated to return {declared}, but "
                     f"`{bad[0][1][:80]}` returns {bad[0][0]}"),
        "caveat": CAVEAT,
        "confidence": "moderate",
    }]


def demo() -> None:
    buggy = ('def loyalty_discount(total, years):\n'
             '    """Members of 3+ years get 15% off."""\n'
             '    if years >= 3:\n'
             '        return round(total * 0.90, 2)\n'
             '    return total\n')
    found = check_function(buggy, "loyalty_discount")
    assert len(found) == 1, found
    assert found[0]["kind"] == "documented_rate_mismatch"
    assert found[0]["documented_percentages"] == [15.0]
    assert 0.9 in found[0]["code_multiplies_by"]
    assert "stale" in found[0]["caveat"]

    # the same function, implemented as documented -> silence
    correct = buggy.replace("0.90", "0.85")
    assert check_function(correct, "loyalty_discount") == []

    # a fee rather than a discount: 15% implies 1.15, and that is accepted too
    fee = ('def with_fee(total):\n'
           '    """Adds a 15% service fee."""\n'
           '    return total * 1.15\n')
    assert check_function(fee, "with_fee") == []

    # the rate applied directly (computing the discount amount, not the remainder)
    amount = ('def discount_amount(total):\n'
              '    """Returns the 15% discount."""\n'
              '    return total * 0.15\n')
    assert check_function(amount, "discount_amount") == []

    # no claim, no check — silence is the right answer, not a guess
    assert check_function("def f(x):\n    return x * 0.9\n", "f") == []
    # a claim with no scaling literal: the rate may come from config, so say nothing
    assert check_function('def g(x):\n    """Applies 15% off."""\n    return x - RATE\n', "g") == []
    # unknown function, unparseable source
    assert check_function(buggy, "nope") == []
    assert check_function("def broken(:\n", "broken") == []

    # bounds: a literal outside the documented range fires; one the docstring names does not
    score = ('def score(x):\n    """Returns a score between 0 and 100."""\n'
             '    if x is None:\n        return -1\n    return min(100, x)\n')
    assert [m["kind"] for m in check_function(score, "score")] == ["documented_bound_violation"]
    assert check_function(score.replace("100.\"", "100, or -1 when x is None.\""), "score") == []

    # idempotence: an unconditional append fires; the guarded form is how it is done right
    reg = ('def register(self, x):\n    """Idempotent."""\n    self.items.append(x)\n')
    assert [m["kind"] for m in check_function(reg, "register")] == ["documented_idempotence_violation"]
    guarded = reg.replace("    self.items", "    if x not in self.items:\n        self.items")
    assert check_function(guarded, "register") == []

    # type preservation: a literal of another type fires; int for float and generators do not
    assert [m["kind"] for m in check_function("def n(x) -> str:\n    return 0\n", "n")] \
        == ["return_type_mismatch"]
    assert check_function("def n(x) -> float:\n    return 0\n", "n") == []
    assert check_function("def n(x) -> 'MyType':\n    return 0\n", "n") == []

    print("lib/spec_check.py OK")


if __name__ == "__main__":
    demo()
