"""False-positive count for `lib.spec_check` over a corpus of correct code (12 §8).

    PYTHONPATH=. python scripts/spec_check_fp.py <dir> [<dir> ...] [--only f1,f2,...]

Runs every invariant over every function and method in the .py files under each directory and
prints, per invariant, how many functions it could judge (the docstring or annotation made a claim)
and how many it fired on. Against a mature, well-tested library every fire is presumed a false
positive until read by hand. `--only` restricts the count to named functions (the trial set).

The Phase 7 gate: an invariant ships only if it fires on zero correct functions in the `humanize`
trial set (docs/writeup/real-repo-trials.md §2):

    git clone https://github.com/python-humanize/humanize && git -C humanize checkout 785e5dc
    PYTHONPATH=. python scripts/spec_check_fp.py humanize/src \\
        --only intword,apnumber,scientific,naturalsize,ordinal,fractional,clamp,metric,intcomma
"""
import argparse
import ast
import os
import sys
from collections import Counter

from lib import spec_check
from lib.intent import documented_numbers

KINDS = {
    "documented_rate_mismatch": lambda n, d: bool(documented_numbers(d)["percentages"]),
    "documented_bound_violation": lambda n, d: bool(spec_check._BOUND.search(d)),
    "documented_idempotence_violation": lambda n, d: bool(spec_check._IDEMPOTENT.search(d)),
    "return_type_mismatch": lambda n, d: spec_check._declared(n.returns) is not None,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    judged, fired, total = Counter(), Counter(), 0
    for root in a.dirs:
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        tree = ast.parse(fh.read())
                except (SyntaxError, ValueError):
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if only and node.name not in only:
                        continue
                    total += 1
                    doc = ast.get_docstring(node) or ""
                    for kind, applies in KINDS.items():
                        judged[kind] += applies(node, doc)
                    for m in spec_check.check_node(node):
                        fired[m["kind"]] += 1
                        print(f"FIRE {m['kind']}: {path}:{node.lineno} {node.name} — {m['evidence']}")
    print(f"\n{total} function(s)")
    for kind in KINDS:
        print(f"  {kind:34s} judged {judged[kind]:5d}   fired {fired[kind]:4d}")
    return 1 if sum(fired.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
