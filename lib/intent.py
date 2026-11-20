"""Stated intent, extracted from the repository (11 §3, Tier 2 input).

The blind oracle's whole value comes from one rule: **the model that states what the code should
do must never see what the code does.** A model shown the implementation restates it — that is the
documented failure of LLM-generated oracles ([arXiv 2410.21136]), and it is how a test asserting a
bug earns a 0.83.

So this module assembles the *intent side* of the comparison and nothing else:

    signature       the contract a caller sees
    docstring       what the author said it does
    type hints      what the author said it accepts and returns
    PR title/body   why this change was made
    call sites      how the rest of the codebase expects it to behave

and it deliberately strips the body. `redact_body` is the safety mechanism, not a formatting
nicety: if it fails open, the tier silently becomes worthless while still reporting confidence.

A deterministic pre-check runs first: `documented_numbers` pulls percentages and constants out of
the docstring, so the commonest mismatch — a docstring promising 15% over code that gives 10% —
can often be caught with no model at all.

    python -m lib.intent        # self-check
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional

# "15%", "15 percent", "0.15" — the forms a docstring states a rate in.
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)


def signature_of(source: str, function: str) -> Optional[str]:
    """The `def` line with its annotations — the contract, without the body."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            args = ast.unparse(node.args)
            returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            return f"{prefix} {function}({args}){returns}:"
    return None


def docstring_of(source: str, function: str) -> Optional[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return ast.get_docstring(node)
    return None


def redact_body(source: str, function: str) -> str:
    """The module with the target function's body replaced by `...`.

    The point of the tier: whatever reaches the blind oracle must not contain the logic it is
    supposed to independently predict. Surrounding code stays — sibling functions and constants are
    context a reviewer would also have — but the answer itself is removed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            doc = ast.get_docstring(node)
            body: List[ast.stmt] = []
            if doc is not None:
                body.append(ast.Expr(value=ast.Constant(value=doc)))
            body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
            node.body = body
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def documented_numbers(docstring: Optional[str]) -> Dict[str, Any]:
    """Numeric claims a docstring makes, for the deterministic pre-check.

    Catches the commonest specification/implementation mismatch without a model: a docstring that
    promises a rate the code does not apply.
    """
    if not docstring:
        return {"percentages": [], "multipliers": []}
    pcts = [float(m) for m in _PERCENT.findall(docstring)]
    # 15% off -> the surviving fraction is 0.85; 15% fee -> 1.15. Both are worth comparing.
    return {"percentages": pcts,
            "multipliers": sorted({round(1 - p / 100, 4) for p in pcts}
                                  | {round(1 + p / 100, 4) for p in pcts})}


def call_sites(sources: Dict[str, str], function: str, limit: int = 5) -> List[Dict[str, str]]:
    """How the rest of the codebase calls it — behaviour the callers already assume."""
    out: List[Dict[str, str]] = []
    needle = re.compile(rf"\b{re.escape(function)}\s*\(")
    for path, src in sources.items():
        for i, line in enumerate(src.splitlines(), start=1):
            if needle.search(line) and not line.lstrip().startswith(("def ", "async def")):
                out.append({"file": path, "line": str(i), "code": line.strip()[:160]})
                if len(out) >= limit:
                    return out
    return out


# An oracle with nothing to read will confabulate, and under this design a dispute outranks every
# other tier — so a guess becomes a false alarm that blocks adoption AND raises a finding against
# healthy code. Observed live on 2026-09-26: a fixture with no docstring and the one-word PR title
# "coupon" produced `disputed` on one run of three and `confirms_intent` on the others, against
# code that was correct. Thin intent must read as NO intent.
MIN_DOCSTRING_CHARS = 20
MIN_DESCRIPTION_CHARS = 40


def is_substantive(doc: Optional[str], description: Optional[str]) -> bool:
    """Is there enough stated intent to derive an expectation from?

    A title is a label, not a specification: "coupon" says nothing about what the function should
    return. Only a docstring or a real description counts, and both have a length floor.
    """
    if doc and len(doc.strip()) >= MIN_DOCSTRING_CHARS:
        return True
    return bool(description and len(description.strip()) >= MIN_DESCRIPTION_CHARS)


def collect(source: str, function: str, event: Optional[Dict[str, Any]] = None,
            other_sources: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Everything the blind oracle is allowed to see. Never includes the target's body."""
    change = ((event or {}).get("change") or {})
    doc = docstring_of(source, function)
    return {
        "signature": signature_of(source, function),
        "docstring": doc,
        "documented_numbers": documented_numbers(doc),
        "module_without_body": redact_body(source, function),
        "pr_title": change.get("title"),
        "pr_description": change.get("description"),
        "call_sites": call_sites(other_sources or {}, function),
        "has_stated_intent": is_substantive(doc, change.get("description")),
    }


def collect_js(source: str, function: str, target_file: str,
               event: Optional[Dict[str, Any]] = None,
               other_sources: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """`collect` for a JS/TS function (Phase 6): the JSDoc block stands in for the docstring.

    Line-based redaction over the tree-sitter span: the signature line and the closing line stay,
    every line between becomes a comment. Returns None when the body could not be removed (the
    function is not found, or its body shares a line with the signature) — the caller must refuse
    rather than show the implementation.
    """
    from lib import jsexec                      # local: jsexec pulls in tree-sitter
    span = jsexec.function_span(source, target_file, function)
    if not span:
        return None
    start, end = span
    lines = source.splitlines()
    if end <= start + 1:
        return None                             # one- or two-line function: no clean cut
    body = "\n".join(lines[start:end - 1]).strip()
    redacted = "\n".join(lines[:start] + ["  /* body removed */"] + lines[end - 1:])
    if body and body in redacted:
        return None
    sig = re.split(r"\{\s*$|=>", lines[start - 1].strip(), maxsplit=1)[0].strip()

    doc = None                                  # the /** ... */ block right above the def
    i = start - 2
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i >= 0 and lines[i].strip().endswith("*/"):
        j = i
        while j >= 0 and "/*" not in lines[j]:
            j -= 1
        if j >= 0:
            raw = "\n".join(lines[j:i + 1])
            doc = "\n".join(re.sub(r"^\s*(/\*\*?|\*/|\*)\s?", "", ln).replace("*/", "").rstrip()
                            for ln in raw.splitlines()).strip() or None
    change = ((event or {}).get("change") or {})
    return {
        "signature": sig,
        "docstring": doc,
        "documented_numbers": documented_numbers(doc),
        "module_without_body": redacted,
        "pr_title": change.get("title"),
        "pr_description": change.get("description"),
        "call_sites": call_sites(other_sources or {}, function.rsplit(".", 1)[-1]),
        "has_stated_intent": is_substantive(doc, change.get("description")),
        "language": "javascript",
    }


def demo() -> None:
    src = ('CAP = 500\n\n\n'
           'def loyalty_discount(total: float, years: int) -> float:\n'
           '    """Members of 3+ years get 15% off. Everyone else pays full price."""\n'
           '    if years >= 3:\n'
           '        return round(total * 0.90, 2)\n'
           '    return total\n\n\n'
           'def other(x):\n'
           '    return x\n')

    assert signature_of(src, "loyalty_discount") == \
        "def loyalty_discount(total: float, years: int) -> float:"
    assert "15%" in docstring_of(src, "loyalty_discount")

    # the safety mechanism: the implementation must be gone, the context kept
    red = redact_body(src, "loyalty_discount")
    assert "0.90" not in red, red
    assert "years >= 3" not in red, red
    assert "15%" in red, "the docstring is intent and must survive"
    assert "CAP = 500" in red and "def other" in red, "surrounding context is kept"

    # the deterministic pre-check: 15% off means the survivor is 0.85, not 0.90
    nums = documented_numbers(docstring_of(src, "loyalty_discount"))
    assert nums["percentages"] == [15.0]
    assert 0.85 in nums["multipliers"] and 1.15 in nums["multipliers"]
    assert 0.90 not in nums["multipliers"], "the implemented 10% is NOT what the doc claims"

    sites = call_sites({"app/checkout.py": "total = loyalty_discount(cart.total, u.years)\n"},
                       "loyalty_discount")
    assert sites and sites[0]["file"] == "app/checkout.py"

    ctx = collect(src, "loyalty_discount",
                  {"change": {"title": "Add loyalty discount", "description": "15% for 3+ years"}})
    assert ctx["has_stated_intent"] is True
    assert "0.90" not in ctx["module_without_body"]

    # no docstring, no PR text -> nothing to compare against, and it says so
    bare = collect("def f(x):\n    return x * 3\n", "f")
    assert bare["has_stated_intent"] is False
    assert bare["documented_numbers"]["percentages"] == []

    # JS/TS: the JSDoc is the intent, the body is gone
    js = ("const CAP = 500;\n\n"
          "/**\n * Orders of more than 10 units get 10% off the unit price.\n */\n"
          "function bulkPrice(qty, unit) {\n  if (qty > 10) {\n    return qty * unit * 0.9;\n  }\n"
          "  return qty * unit;\n}\n\nmodule.exports = { bulkPrice };\n")
    jctx = collect_js(js, "bulkPrice", "src/pricing.js")
    assert jctx and jctx["has_stated_intent"] is True, jctx
    assert "10% off" in jctx["docstring"] and "*" not in jctx["docstring"], jctx["docstring"]
    assert jctx["signature"] == "function bulkPrice(qty, unit)", jctx["signature"]
    red_js = jctx["module_without_body"]
    assert "0.9" not in red_js and "qty > 10" not in red_js, red_js
    assert "CAP = 500" in red_js and "module.exports" in red_js
    assert collect_js("const f = (x) => x * 3;\n", "f", "a.js") is None, "no clean cut -> refuse"

    print("lib/intent.py OK")


if __name__ == "__main__":
    demo()
