"""lib/triage.py — deterministic exclusion + dangerous-sink detection + hotspot ranking.

Shared by `secret_scanner_tool` (A2) and `review_planner_tool` (A8). Pure stdlib, no I/O.
Three jobs, all deterministic (rule D2 — the LLM never decides any of this):

1. **Exclusion** — drop vendored/generated files from the reviewable surface so a full-repo
   audit doesn't waste the review budget on `node_modules`/lockfiles/minified bundles.
2. **Dangerous-sink rules** — a size-independent detection *floor*: every match is a finding
   regardless of repo size or LLM budget. (Hardcoded-secret rules stay in `secret_scanner`;
   these are the injection/deserialization/crypto sinks.)
3. **Hotspot ranking** — score each added line (sink hit + sensitive-path + entropy) so a
   bounded LLM budget is spent on the riskiest lines, not the first N by file-walk order.

Self-check: `python lib/triage.py`.
"""
from __future__ import annotations

import fnmatch
import math
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

# git empty-tree object hash — audit mode diffs against this so the whole repo reads as added.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Vendored / generated directories excluded if ANY path segment matches (fnmatch's `*` doesn't
# span leading segments cleanly, so directories are matched by segment membership, not globs).
DEFAULT_EXCLUDE_DIRS: List[str] = [
    "node_modules", "dist", "build", "vendor", "target",
    ".git", "__pycache__", ".venv", "venv", "migrations", "__snapshots__",
]
# Generated / binary-ish file names, matched against the basename.
DEFAULT_EXCLUDE_GLOBS: List[str] = [
    "*.min.js", "*.min.css", "*.map", "*.svg", "*.snap", "*.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
]

# (category, regex, cwe, severity, title) — first match wins per line. Tuned to avoid firing on
# benign code: the SQL rules require a query keyword AND string concatenation/formatting, not any
# f-string; sinks are concrete API shapes, not bare keywords.
SINK_RULES: List[Tuple[str, "re.Pattern[str]", str, str, str]] = [
    ("code_injection", re.compile(r"\b(?:eval|exec)\s*\("), "CWE-95", "high",
     "Dynamic code execution (eval/exec)"),
    ("command_injection", re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "CWE-78", "high",
     "subprocess called with shell=True"),
    ("command_injection", re.compile(r"\bos\.system\s*\("), "CWE-78", "high", "os.system call"),
    ("sql_injection",
     re.compile(r"""(?i)\b(?:select|insert|update|delete)\b.*?["'].*?["']\s*(?:\+|%|\.format\()"""),
     "CWE-89", "high", "SQL query built by string concatenation/formatting"),
    ("sql_injection",
     re.compile(r"""(?i)(?:execute|executemany|\.query)\s*\(\s*f["']"""),
     "CWE-89", "high", "SQL executed from an f-string"),
    ("unsafe_deserialization", re.compile(r"\b(?:pickle|cPickle)\.loads?\s*\("), "CWE-502", "high",
     "Unsafe pickle deserialization"),
    ("unsafe_deserialization", re.compile(r"\byaml\.load\s*\((?![^)]*Safe)"), "CWE-502", "high",
     "yaml.load without SafeLoader"),
    ("xss", re.compile(r"\b(?:innerHTML|dangerouslySetInnerHTML|document\.write)\b"), "CWE-79",
     "medium", "Potential DOM XSS sink"),
    ("weak_crypto", re.compile(r"(?i)\bhashlib\.(?:md5|sha1)\s*\("), "CWE-327", "medium",
     "Weak hash function (md5/sha1)"),
    ("tls_verification_disabled", re.compile(r"\bverify\s*=\s*False\b"), "CWE-295", "high",
     "TLS certificate verification disabled"),
]

# High-entropy literal heuristic (same threshold secret_scanner uses).
_QUOTED = re.compile(r"['\"]([^'\"]{20,})['\"]")

# Lines that are never a hotspot (imports, blanks, pure comments, module boilerplate).
_TRIVIA = re.compile(
    r"^\s*(?:$|#|//|/\*|\*|\"\"\"|import\b|from\b\s+\S+\s+import\b|require\(|export\b|package\b|use\s)"
)

_SEV_WEIGHT = {"critical": 5.0, "high": 4.0, "medium": 2.0, "low": 1.0}


def iter_added_lines(profile: Dict[str, Any]) -> Iterator[Tuple[str, int, str]]:
    """(path, new_line_number, content) for every added ('+') line in the diff hunks."""
    for f in profile.get("files", []):
        for h in f.get("hunks", []):
            offset = 0
            for line in h.get("patch", "").splitlines()[1:]:  # skip the @@ header
                if line.startswith("+") and not line.startswith("+++"):
                    yield f["path"], h.get("new_start", 0) + offset, line[1:]
                    offset += 1
                elif not line.startswith("-"):
                    offset += 1  # context line advances the new-file counter (none under -U0)


def entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((n / len(s)) * math.log2(n / len(s))
                for n in (s.count(c) for c in set(s)))


def is_excluded(path: str, extra_globs: Optional[List[str]] = None) -> bool:
    p = path.replace("\\", "/")
    segments = p.split("/")
    if any(seg in DEFAULT_EXCLUDE_DIRS for seg in segments):
        return True
    base = segments[-1]
    for g in DEFAULT_EXCLUDE_GLOBS + list(extra_globs or []):
        if fnmatch.fnmatch(p, g) or fnmatch.fnmatch(base, g):
            return True
    return False


def scan_sink(content: str) -> Optional[Tuple[str, str, str, str]]:
    """First (category, cwe, severity, title) sink match on a line, or None."""
    for cat, rx, cwe, sev, title in SINK_RULES:
        if rx.search(content):
            return cat, cwe, sev, title
    return None


def score_line(path: str, content: str, sensitive_files: set) -> Tuple[float, List[str]]:
    """Hotspot score + reasons for one added line. Higher = riskier / more worth LLM attention."""
    score = 0.0
    why: List[str] = []
    hit = scan_sink(content)
    if hit:
        score += _SEV_WEIGHT.get(hit[2], 1.0)
        why.append(hit[0])
    if path in sensitive_files:
        score += 2.0
        why.append("sensitive_path")
    m = _QUOTED.search(content)
    if m and entropy(m.group(1)) >= 4.0:
        score += 1.5
        why.append("high_entropy")
    if _TRIVIA.match(content):
        score -= 5.0  # never let boilerplate outrank real code
    return score, why


def scored_lines(lines: Iterator[Tuple[str, int, str]], sensitive_files: set,
                 extra_globs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """All non-excluded added lines with a hotspot score, deterministically ordered (score desc)."""
    out: List[Dict[str, Any]] = []
    for path, lineno, content in lines:
        if is_excluded(path, extra_globs):
            continue
        s, why = score_line(path, content, sensitive_files)
        out.append({"file": path, "line": lineno, "code": content, "score": s, "why": why})
    out.sort(key=lambda d: (-d["score"], d["file"], d["line"]))
    return out


def rank(lines: Iterator[Tuple[str, int, str]], sensitive_files: set, budget: int,
         extra_globs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Top-`budget` hotspot lines (deterministic). Context lines are added by the caller."""
    return scored_lines(lines, sensitive_files, extra_globs)[: max(0, budget)]


def demo() -> None:
    # exclusions
    assert is_excluded("node_modules/left-pad/index.js")
    assert is_excluded("app/bundle.min.js")
    assert is_excluded("package-lock.json")
    assert not is_excluded("src/app/payments.py")
    assert is_excluded("src/generated/x.py", extra_globs=["**/generated/**"])

    # every sink rule fires on a representative line
    sink_samples = [
        "eval(user_input)",
        "subprocess.run(cmd, shell=True)",
        "os.system('rm -rf ' + path)",
        "q = \"SELECT id FROM users WHERE name = '\" + u + \"'\"",
        "cur.execute(f\"SELECT * FROM t WHERE id={x}\")",
        "data = pickle.loads(blob)",
        "cfg = yaml.load(open('c.yml'))",
        "el.innerHTML = untrusted",
        "h = hashlib.md5(pw).hexdigest()",
        "requests.get(url, verify=False)",
    ]
    for line in sink_samples:
        assert scan_sink(line) is not None, f"sink rule missed: {line}"

    # benign code (incl. the B4 happy-run added lines) must NOT trip a sink
    for line in ["def subtract(a, b):", "    return a - b", "x = a + b",
                 "name = 'hello world here'", "return c.execute('SELECT 1 WHERE u=?', (u,))"]:
        assert scan_sink(line) is None, f"false positive sink on: {line}"

    # ranking: deterministic + sink outranks an import
    profile = {"files": [{"path": "auth/login.py", "hunks": [{"new_start": 1, "patch":
        "@@\n+import os\n+q = \"SELECT id FROM u WHERE n = '\" + u + \"'\"\n+x = 1\n"}]}]}
    lines = list(iter_added_lines(profile))
    assert len(lines) == 3
    r1 = rank(iter(lines), {"auth/login.py"}, 10)
    r2 = rank(iter(lines), {"auth/login.py"}, 10)
    assert [d["line"] for d in r1] == [d["line"] for d in r2], "rank not deterministic"
    assert "sql_injection" in r1[0]["why"], "sink line should rank first"
    assert r1[-1]["code"].strip() == "import os", "import should rank last"

    # entropy
    assert entropy("aaaaaaaa") < 1.0
    assert entropy("Xq7#Lm92Zp!aB3vR") > 3.0
    print("lib/triage.py OK")


if __name__ == "__main__":
    demo()
