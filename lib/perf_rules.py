"""Deterministic performance smells over added lines (08 §3 P2.2).

The floor for `performance_review_agent`, in the same shape `lib/triage` gives the security
reviewer: rules that fire from code, never from an LLM's opinion, so a reviewer that drifts or
stalls cannot turn a slow loop into a clean report (rule 4).

Deliberately NOT part of `triage.SINK_RULES`: that list feeds the *security* floor and the hotspot
ranking that sizes the review fan-out. A query in a loop is a cost problem, not a vulnerability,
and mixing the two would inflate security coverage numbers with things nobody gets paged for.

Context matters here in a way it does not for secrets — `db.query(...)` is fine, and the same call
one indent inside a `for` is an N+1. So each file is read once, turned into a per-line context
(loop depth, enclosing async function), and only then are the rules applied to the added lines.

    python -m lib.perf_rules        # self-check
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

# ---------------------------------------------------------------- context scanning

# A loop header in either language family. JS `for (...) {` and Python `for x in y:` both match.
_LOOP = re.compile(r"^\s*(?:async\s+)?(?:for|while)\b")
# Function headers we care about: only async ones create a "blocking call is a bug here" context.
_ASYNC_DEF = re.compile(r"^\s*(?:async\s+def\s+\w+|async\s+function\b|\w+\s*:\s*async\s*\(|"
                        r"(?:const|let|var)\s+\w+\s*=\s*async\s*[(\w])")
_SYNC_DEF = re.compile(r"^\s*(?:def\s+\w+|function\s+\w+|class\s+\w+)")
_BLANK_OR_COMMENT = re.compile(r"^\s*(?:#|//|/\*|\*|$)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def line_contexts(text: str) -> List[Tuple[int, bool]]:
    """Per-line (loop_depth, inside_async_fn), 0-indexed by line.

    ponytail: indentation-based, not a parse. Correct for formatted Python and for the
    conventionally-indented JS/TS this repo sees; a minified or brace-salad file reads as depth 0,
    i.e. it under-reports rather than inventing findings. Upgrade path if that bites: reuse
    `lib.ts_parse` for JS/TS and `ast` for Python, which both already run in this project.
    """
    out: List[Tuple[int, bool]] = []
    loops: List[int] = []        # indents of open loop headers
    async_fn: int | None = None  # indent of the enclosing async def, if any
    for raw in text.splitlines():
        ind = _indent(raw)
        if not _BLANK_OR_COMMENT.match(raw):
            # A line at or left of an open block's indent closes it.
            loops = [i for i in loops if i < ind]
            if async_fn is not None and ind <= async_fn:
                async_fn = None
            if _ASYNC_DEF.match(raw):
                async_fn = ind
            elif _SYNC_DEF.match(raw) and (async_fn is None or ind <= async_fn):
                async_fn = None
        out.append((len(loops), async_fn is not None))
        # The header itself is not "inside" its own body, so open it after recording.
        if not _BLANK_OR_COMMENT.match(raw) and _LOOP.match(raw):
            loops.append(ind)
    return out


# ---------------------------------------------------------------- rules
# (category, pattern, severity, title, needs_loop, needs_async)
# Severity caps at medium by design (08 §5): a performance smell is not a security hole, and the
# risk formula must keep "critical" meaning what it means today. The LLM reviewer may argue a hot
# path deserves high — that judgment is exactly what it is there for.
PERF_RULES: List[Tuple[str, "re.Pattern[str]", str, str, bool, bool]] = [
    ("n_plus_one",
     re.compile(r"(?i)\b(?:\.(?:query|execute|fetchall|fetchone|find_one|find|aggregate|get_or_create)"
                r"|session\.(?:query|get)|objects\.(?:get|filter)|db\.(?:collection|table))\s*\("),
     "medium", "Database query inside a loop", True, False),
    ("n_plus_one_http",
     re.compile(r"(?i)\b(?:requests\.(?:get|post|put|delete|patch)|httpx\.(?:get|post)|urlopen|"
                r"fetch|axios\.(?:get|post|put|delete))\s*\("),
     "medium", "Network call inside a loop", True, False),
    ("sequential_await",
     re.compile(r"(?<![\w.])await\s+\w"),
     "medium", "Sequential await inside a loop", True, False),
    ("blocking_in_async",
     re.compile(r"(?i)\b(?:time\.sleep|requests\.(?:get|post|put|delete|patch)|"
                r"subprocess\.(?:run|call|check_output)|urlopen|"
                r"(?:fs|require\(['\"]fs['\"]\))\.\w*Sync)\s*\("),
     "medium", "Blocking call inside an async function", False, True),
    ("unbounded_query",
     re.compile(r"""(?i)select\s+\*\s+from\s+\w+(?![^'"]*\blimit\b)"""),
     "low", "SELECT * without a LIMIT", False, False),
]

# Concatenating onto a collection inside a nested loop — the classic accidental O(n^2). Kept
# separate because it needs a loop DEPTH, not merely "in a loop".
_QUADRATIC = re.compile(r"(?:\.append\s*\(|\.push\s*\(|\+=\s*\[|\.concat\s*\()")


def scan_file(path: str, text: str, added: Iterable[int]) -> List[Dict[str, Any]]:
    """Findings for the ADDED lines of one file (line numbers are 1-based, as in a diff)."""
    ctx = line_contexts(text)
    lines = text.splitlines()
    found: List[Dict[str, Any]] = []
    for ln in sorted(set(added)):
        if ln < 1 or ln > len(lines):
            continue
        code = lines[ln - 1]
        if _BLANK_OR_COMMENT.match(code):
            continue
        depth, in_async = ctx[ln - 1]
        for cat, rx, sev, title, needs_loop, needs_async in PERF_RULES:
            if needs_loop and depth < 1:
                continue
            if needs_async and not in_async:
                continue
            if rx.search(code):
                found.append(_finding(cat, sev, title, path, ln, code))
                break  # one finding per line: the first rule is the most specific
        else:
            if depth >= 2 and _QUADRATIC.search(code):
                found.append(_finding("quadratic_growth", "medium",
                                      "Collection grown inside a nested loop", path, ln, code))
    return found


_FIX = {
    "n_plus_one": "Fetch the rows once outside the loop (join, IN-clause or batch load).",
    "n_plus_one_http": "Batch the requests, or issue them concurrently and gather the results.",
    "sequential_await": "Collect the awaitables and await them together (asyncio.gather / Promise.all).",
    "blocking_in_async": "Use the async equivalent, or run the blocking call in a thread executor.",
    "unbounded_query": "Add a LIMIT (and pagination) so the result set cannot grow with the table.",
    "quadratic_growth": "Build the collection in one pass, or use a set/dict lookup instead of rescanning.",
}


def _finding(cat: str, sev: str, title: str, path: str, ln: int, code: str) -> Dict[str, Any]:
    return {
        "id": f"PERF-{cat}-{path}:{ln}",   # stable across runs; report_publisher renumbers on merge
        "category": cat,
        "severity": sev,
        "file": path,
        "line_start": ln,
        "line_end": ln,
        "title": title,
        "explanation": f"{title} — `{code.strip()[:160]}`",
        "fix_suggestion": _FIX[cat],
        "source": "tool",
    }


def demo() -> None:
    py = (
        "import time\n"
        "def load(ids, db):\n"
        "    rows = []\n"
        "    for i in ids:\n"
        "        rows.append(db.query(i))\n"          # 5: n_plus_one
        "    return rows\n"
        "\n"
        "def fine(db, ids):\n"
        "    return db.query(ids)\n"                  # 9: benign — same call, no loop
        "\n"
        "async def handler(urls):\n"
        "    time.sleep(1)\n"                         # 12: blocking_in_async
        "    out = []\n"
        "    for u in urls:\n"
        "        out.append(await compute(u))\n"      # 15: sequential_await (no HTTP call in it)
        "    return out\n"
        "\n"
        "def pairs(xs, ys):\n"
        "    acc = []\n"
        "    for x in xs:\n"
        "        for y in ys:\n"
        "            acc.append((x, y))\n"            # 22: quadratic_growth
        "    return acc\n"
    )
    all_lines = range(1, len(py.splitlines()) + 1)
    got = {f["line_start"]: f["category"] for f in scan_file("a.py", py, all_lines)}
    assert got.get(5) == "n_plus_one", got
    assert 9 not in got, f"benign query outside a loop must not fire: {got}"
    assert got.get(12) == "blocking_in_async", got
    assert got.get(15) == "sequential_await", got
    assert got.get(22) == "quadratic_growth", got

    # only ADDED lines are reported, exactly like the security floor
    assert [f["line_start"] for f in scan_file("a.py", py, [9, 22])] == [22]

    # a sync function's blocking call is not a finding
    sync = "def job():\n    time.sleep(1)\n"
    assert scan_file("b.py", sync, [1, 2]) == []

    # async context closes at dedent: the sleep below belongs to a sync function
    closes = "async def a():\n    await x()\n\ndef b():\n    time.sleep(1)\n"
    assert scan_file("c.py", closes, [5]) == [], scan_file("c.py", closes, [5])

    # JS/TS: braces are conventionally indented, so the same context walk applies
    js = ("async function all(ids) {\n"
          "  const out = [];\n"
          "  for (const id of ids) {\n"
          "    out.push(await axios.get(`/i/${id}`));\n"   # 4
          "  }\n"
          "  return out;\n"
          "}\n")
    cats = {f["line_start"]: f["category"] for f in scan_file("a.js", js, range(1, 8))}
    assert cats.get(4) in ("sequential_await", "n_plus_one_http"), cats

    print("lib/perf_rules.py OK")


if __name__ == "__main__":
    demo()
