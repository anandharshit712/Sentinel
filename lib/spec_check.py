"""Deterministic specification checks: does the code do what its docstring says? (11 §3, Tier 3)

The blind oracle (Tier 2) is the general answer to "is the expectation right", and it needs a
model. This module takes the commonest instance of that question and answers it with arithmetic:

    \"\"\"Members of 3+ years get 15% off.\"\"\"     # the claim
    return round(total * 0.90, 2)                 # the code applies 10%

A docstring that states a rate implies a multiplier — 15% off implies 0.85, a 15% fee implies 1.15.
If the function multiplies by a constant and that constant matches neither, the code and its
documentation disagree, and a test generated from the code will silently take the code's side.

Static only: it reads literals, it never executes anything. That keeps it usable on code nobody has
vetted, and it means the check costs nothing and cannot flake.

**Every finding here is advisory.** A mismatch means the code and the docs disagree, not which one
is wrong — the docstring may simply be stale. That distinction belongs to a human, so the output
names both sides and never picks one.

    python -m lib.spec_check        # self-check
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

from lib.intent import docstring_of, documented_numbers

TOLERANCE = 0.005          # 0.85 vs 0.849 is the same claim; 0.85 vs 0.90 is not
MIN_MULTIPLIER = 0.01      # ignore unit conversions and tiny scaling factors
MAX_MULTIPLIER = 100.0


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


def check_function(source: str, function: str) -> List[Dict[str, Any]]:
    """Mismatches between what `function`'s docstring claims and what its code applies."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    target: Optional[ast.AST] = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            target = node
            break
    if target is None:
        return []

    doc = docstring_of(source, function)
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
        # Which side is wrong is not knowable from here, and pretending otherwise would be the same
        # overclaim this whole document exists to remove.
        "caveat": ("This says the code and its documentation disagree, not which one is right — "
                   "the docstring may be stale. A test generated from the code will assert the "
                   "code's behaviour either way."),
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

    print("lib/spec_check.py OK")


if __name__ == "__main__":
    demo()
