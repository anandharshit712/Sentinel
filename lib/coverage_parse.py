"""Parse coverage reports into one shape (09 §4 P3.1).

Three formats, because they are what projects already produce — coverage.py JSON (`coverage json`,
`pytest --cov --cov-report=json`), Cobertura XML (`--cov-report=xml`, and what most CI uploads),
and lcov (Istanbul/nyc, the JS default). Sentinel asks the target project for no new tooling; it
reads whatever the project's own runner already emits.

Everything normalises to `{relative_posix_path: {line_number: hit_count}}`. A line absent from the
map was never instrumented (not executable, or the file was not measured); a line present with 0
was instrumented and never run. That distinction is the whole basis of a coverage *gap*, so it is
preserved rather than flattened to a boolean.

Parsing is best-effort by design: a coverage report is a bonus signal, and a malformed or partial
one must never take down a run that has already produced its test results. Every parser returns
`{}` rather than raising.

    python -m lib.coverage_parse        # self-check
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict

logger = logging.getLogger("lib.coverage_parse")

CoverageMap = Dict[str, Dict[int, int]]


def _norm(path: str, root: str | None = None) -> str:
    """Repo-relative POSIX path, so a coverage report and a git diff agree on file identity."""
    p = path.replace("\\", "/")
    if root:
        r = root.replace("\\", "/").rstrip("/")
        if p.lower().startswith(r.lower() + "/"):
            p = p[len(r) + 1:]
    p = re.sub(r"^\./", "", p)
    return p.lstrip("/")


def parse_coverage_json(text: str, root: str | None = None) -> CoverageMap:
    """coverage.py JSON: files.<path>.executed_lines / missing_lines."""
    out: CoverageMap = {}
    try:
        data = json.loads(text)
        for path, entry in (data.get("files") or {}).items():
            lines: Dict[int, int] = {}
            for ln in entry.get("executed_lines") or []:
                lines[int(ln)] = 1
            for ln in entry.get("missing_lines") or []:
                lines.setdefault(int(ln), 0)
            if lines:
                out[_norm(path, root)] = lines
    except Exception as e:
        logger.info("coverage json unparseable: %s", e)
    return out


def parse_cobertura_xml(text: str, root: str | None = None) -> CoverageMap:
    """Cobertura XML: <class filename=...><lines><line number= hits=/>."""
    out: CoverageMap = {}
    try:
        tree = ET.fromstring(text)
        # <sources><source>/abs/prefix</source> is stripped from filenames when present.
        sources = [s.text.strip() for s in tree.findall(".//sources/source") if (s.text or "").strip()]
        for cls in tree.findall(".//class"):
            fn = cls.get("filename") or ""
            if not fn:
                continue
            path = fn
            for src in sources:
                path = _norm(path, src)
            lines = out.setdefault(_norm(path, root), {})
            for line in cls.findall(".//line"):
                try:
                    n, hits = int(line.get("number", 0)), int(line.get("hits", 0))
                except ValueError:
                    continue
                if n:
                    lines[n] = max(lines.get(n, 0), hits)
    except Exception as e:
        logger.info("cobertura xml unparseable: %s", e)
    return {k: v for k, v in out.items() if v}


def parse_lcov(text: str, root: str | None = None) -> CoverageMap:
    """lcov.info: SF:<path> ... DA:<line>,<hits> ... end_of_record."""
    out: CoverageMap = {}
    try:
        cur: Dict[int, int] | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("SF:"):
                cur = out.setdefault(_norm(line[3:], root), {})
            elif line.startswith("DA:") and cur is not None:
                n, _, h = line[3:].partition(",")
                try:
                    cur[int(n)] = max(cur.get(int(n), 0), int(h.split(",")[0]))
                except ValueError:
                    continue
            elif line == "end_of_record":
                cur = None
    except Exception as e:
        logger.info("lcov unparseable: %s", e)
    return {k: v for k, v in out.items() if v}


def load(path: str, root: str | None = None) -> CoverageMap:
    """Parse a coverage file, choosing the parser by extension then by content sniffing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        logger.info("coverage file unreadable (%s): %s", path, e)
        return {}
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_coverage_json(text, root)
    if ext == ".xml":
        return parse_cobertura_xml(text, root)
    if ext in (".info", ".lcov"):
        return parse_lcov(text, root)
    head = text.lstrip()[:200]
    if head.startswith("{"):
        return parse_coverage_json(text, root)
    if head.startswith("<"):
        return parse_cobertura_xml(text, root)
    return parse_lcov(text, root)


def line_status(cov: CoverageMap, path: str, line: int) -> str:
    """'covered' | 'uncovered' | 'unmeasured' for one line."""
    lines = cov.get(_norm(path))
    if not lines or line not in lines:
        return "unmeasured"
    return "covered" if lines[line] > 0 else "uncovered"


def summarise(cov: CoverageMap) -> Dict[str, Any]:
    """Overall executable/covered counts and percentage — for the coverage_delta on test_results."""
    total = sum(len(v) for v in cov.values())
    hit = sum(1 for v in cov.values() for h in v.values() if h > 0)
    return {"files": len(cov), "lines": total, "covered": hit,
            "line_pct": round(100.0 * hit / total, 1) if total else 0.0}


def demo() -> None:
    cj = json.dumps({"files": {
        "app/orders.py": {"executed_lines": [1, 2, 5], "missing_lines": [3, 4]},
        "app/empty.py": {"executed_lines": [], "missing_lines": []},
    }})
    m = parse_coverage_json(cj)
    assert m["app/orders.py"] == {1: 1, 2: 1, 5: 1, 3: 0, 4: 0}, m
    assert "app/empty.py" not in m, "a file with no executable lines carries no information"

    xml = """<?xml version="1.0"?><coverage><sources><source>/build/repo</source></sources>
      <packages><package><classes>
        <class filename="/build/repo/app/orders.py"><lines>
          <line number="1" hits="3"/><line number="3" hits="0"/>
        </lines></class>
      </classes></package></packages></coverage>"""
    mx = parse_cobertura_xml(xml)
    assert mx == {"app/orders.py": {1: 3, 3: 0}}, mx

    lcov = "SF:src/cart.js\nDA:1,2\nDA:2,0\nend_of_record\nSF:src/x.js\nDA:1,1\nend_of_record\n"
    ml = parse_lcov(lcov)
    assert ml == {"src/cart.js": {1: 2, 2: 0}, "src/x.js": {1: 1}}, ml

    # the three-way distinction the gap analysis depends on
    assert line_status(m, "app/orders.py", 1) == "covered"
    assert line_status(m, "app/orders.py", 3) == "uncovered"
    assert line_status(m, "app/orders.py", 99) == "unmeasured"
    assert line_status(m, "nope.py", 1) == "unmeasured"

    # windows paths and ./ prefixes resolve to the same key as a diff would produce
    assert _norm("app\\orders.py") == "app/orders.py"
    assert _norm("./app/orders.py") == "app/orders.py"
    assert _norm("C:/ws/app/orders.py", "C:/ws") == "app/orders.py"

    assert summarise(m) == {"files": 1, "lines": 5, "covered": 3, "line_pct": 60.0}, summarise(m)

    # malformed input is silence, never an exception — coverage must not fail a finished run
    assert parse_coverage_json("{not json") == {}
    assert parse_cobertura_xml("<broken") == {}
    assert parse_lcov("garbage\nDA:x,y\n") == {}
    assert load("no/such/file.json") == {}

    print("lib/coverage_parse.py OK")


if __name__ == "__main__":
    demo()
