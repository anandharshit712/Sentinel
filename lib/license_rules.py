"""Deterministic compliance floor: licensing, PII exposure, deprecated APIs (08 §3 P2.3).

The floor for `compliance_review_agent`, same contract as every other floor in this project: it
fires from code, so a drifting reviewer cannot produce a clean compliance report (rule 4).

**What this deliberately does not do.** Resolving the licence of each declared dependency needs a
registry lookup (PyPI/npm) — a network call per dependency, from inside a coded tool, on every run.
That is a real feature with real caching and offline-failure questions, and it is not this phase.
What IS provable offline is checked here:

  * the project's own declared licence (manifest field or LICENSE file),
  * copyleft notices in the changed files themselves — vendored GPL/AGPL source, which is the
    licensing accident that actually reaches a diff,
  * a manifest licence field changed to something copyleft,
  * PII written to logs or URLs,
  * deprecated APIs.

`licenses_seen` is reported so the report says what was actually inspected instead of implying a
full dependency audit.

    python -m lib.license_rules        # self-check
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Tuple

# SPDX-ish families. Only what a diff can plausibly contain; a full SPDX matrix is overkill for a
# check whose input is a licence *notice*, not a resolved dependency graph.
COPYLEFT = ("agpl", "gpl-3", "gpl-2", "gplv3", "gplv2", "gnu general public license",
            "gnu affero", "sspl", "cc-by-sa", "cc by-sa")
WEAK_COPYLEFT = ("lgpl", "mpl-2", "mozilla public license", "epl-2", "eclipse public license")
PERMISSIVE = ("mit", "apache-2", "apache license", "bsd-2", "bsd-3", "isc", "unlicense", "0bsd")

_NOTICE = re.compile(
    r"(?i)(gnu\s+(?:affero\s+)?general\s+public\s+license|"
    r"\bSPDX-License-Identifier:\s*(AGPL|GPL|SSPL|CC-BY-SA)[\w.\-]*|"
    r"licensed under the (?:gnu )?(?:affero )?gpl)")

# PII written where it will be persisted or transmitted in the clear.
_LOG_CALL = re.compile(r"(?i)\b(?:log(?:ger|ging)?|console)\s*\.\s*(?:debug|info|warn|warning|error|log)\s*\(|"
                       r"\bprint\s*\(")
_PII_TERM = re.compile(r"(?i)\b(?:email|e_mail|ssn|social_security|passport|credit_card|card_number|"
                       r"cardnumber|cvv|iban|phone_number|date_of_birth|dob|password|passwd|"
                       r"api_key|access_token|auth_token|session_token)\b")
# The same terms interpolated into a URL or query string — PII in a URL lands in every access log.
_PII_IN_URL = re.compile(r"(?i)[?&](?:email|ssn|token|password|card(?:_?number)?|phone)=")

# (category, pattern, severity, title)
DEPRECATED: List[Tuple[str, "re.Pattern[str]", str, str]] = [
    ("deprecated_api", re.compile(r"\bdatetime\.utcnow\s*\("), "low",
     "datetime.utcnow() is deprecated (naive UTC)"),
    ("deprecated_api", re.compile(r"\bssl\.wrap_socket\s*\("), "low",
     "ssl.wrap_socket() was removed in Python 3.12"),
    ("deprecated_api", re.compile(r"^\s*import\s+(?:imp|distutils)\b|from\s+distutils\b"), "low",
     "imp / distutils are removed from the stdlib"),
    ("deprecated_api", re.compile(r"\bnew\s+Buffer\s*\("), "low",
     "new Buffer() is deprecated and unsafe (use Buffer.from)"),
    ("deprecated_api", re.compile(r"\bcomponentWill(?:Mount|ReceiveProps|Update)\b"), "low",
     "Legacy React lifecycle method"),
    ("deprecated_api", re.compile(r"\bsubstr\s*\("), "low",
     "String.substr() is a deprecated Annex-B feature (use slice)"),
]

_MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")
# A manifest's own licence field being set/changed, e.g. `"license": "GPL-3.0-only",` — the notice
# regex does not match this, and it is the line that silently relicenses a project.
_MANIFEST_LICENSE = re.compile(r"""(?i)["']?licen[cs]e["']?\s*[:=]\s*["']?([A-Za-z0-9.\-+ ]{2,40})""")


def classify(text: str) -> str:
    """'copyleft' | 'weak_copyleft' | 'permissive' | 'unknown' for a licence string or notice."""
    low = text.lower()
    # Weak copyleft is tested FIRST: "lgpl-2.1" contains "gpl-2", so the broader family would
    # otherwise swallow it and report an LGPL dependency as a hard conflict.
    for fam, name in ((WEAK_COPYLEFT, "weak_copyleft"), (COPYLEFT, "copyleft"),
                      (PERMISSIVE, "permissive")):
        if any(tok in low for tok in fam):
            return name
    return "unknown"


def project_license(files: Dict[str, str]) -> Tuple[str, str]:
    """(declared licence text, family) from a {path: content} map of the repo's metadata files."""
    for path, content in files.items():
        base = path.replace("\\", "/").rsplit("/", 1)[-1]
        if base == "package.json":
            try:
                lic = (json.loads(content) or {}).get("license")
            except (ValueError, TypeError):
                lic = None
            if isinstance(lic, str) and lic.strip():
                return lic.strip(), classify(lic)
        elif base == "pyproject.toml":
            m = re.search(r'(?im)^\s*license\s*=\s*[\{"\']?\s*(?:text\s*=\s*["\'])?([^"\'\}\n]+)', content)
            if m:
                return m.group(1).strip(), classify(m.group(1))
    for path, content in files.items():
        if path.replace("\\", "/").rsplit("/", 1)[-1].upper().startswith("LICEN"):
            head = content[:2000]
            return head.strip().splitlines()[0][:80] if head.strip() else "LICENSE", classify(head)
    return "", "unknown"


def scan_file(path: str, text: str, added: Iterable[int],
              project_family: str = "unknown") -> List[Dict[str, Any]]:
    """Findings for the ADDED lines of one file."""
    lines = text.splitlines()
    out: List[Dict[str, Any]] = []
    is_manifest = path.replace("\\", "/").rsplit("/", 1)[-1] in _MANIFESTS
    for ln in sorted(set(added)):
        if ln < 1 or ln > len(lines):
            continue
        code = lines[ln - 1]

        if is_manifest:
            m = _MANIFEST_LICENSE.search(code)
            if m and classify(m.group(1)) in ("copyleft", "weak_copyleft"):
                fam = classify(m.group(1))
                out.append(_finding(
                    "license_conflict", "high" if fam == "copyleft" else "medium",
                    f"Manifest declares a {fam.replace('_', ' ')} licence", path, ln, code,
                    "Changing the declared licence changes the terms every consumer of this "
                    "project is bound by."))
                continue

        if _NOTICE.search(code):
            # Copyleft text arriving in the diff. High when the project is permissive (a real
            # licence conflict); medium otherwise, because it still needs an attribution decision.
            sev = "high" if project_family == "permissive" else "medium"
            where = "manifest" if is_manifest else "source file"
            out.append(_finding(
                "license_conflict", sev, f"Copyleft licence notice in a changed {where}", path, ln, code,
                f"The project declares a {project_family} licence. Copyleft-licensed code cannot be "
                "combined with it without adopting the stronger licence."))
            continue

        if _PII_IN_URL.search(code):
            out.append(_finding("pii_exposure", "high", "Personal data passed in a URL", path, ln, code,
                                "URLs are recorded in access logs, proxies and browser history."))
            continue

        if _LOG_CALL.search(code) and _PII_TERM.search(code):
            out.append(_finding("pii_exposure", "high", "Personal data written to a log", path, ln, code,
                                "Logs are retained and widely readable; this is the most common "
                                "GDPR/PCI finding in code review."))
            continue

        for cat, rx, sev, title in DEPRECATED:
            if rx.search(code):
                out.append(_finding(cat, sev, title, path, ln, code,
                                    "Deprecated APIs break on a future runtime upgrade."))
                break
    return out


_FIX = {
    "license_conflict": "Remove the copyleft code, replace it with a permissively licensed "
                        "equivalent, or take a licence decision before merging.",
    "pii_exposure": "Log an opaque identifier instead of the personal data, or redact the field.",
    "deprecated_api": "Move to the supported replacement.",
}


def _finding(cat: str, sev: str, title: str, path: str, ln: int, code: str,
             why: str) -> Dict[str, Any]:
    return {
        "id": f"COMP-{cat}-{path}:{ln}",
        "category": cat,
        "severity": sev,
        "file": path,
        "line_start": ln,
        "line_end": ln,
        "title": title,
        "explanation": f"{why} Line: `{code.strip()[:160]}`",
        "fix_suggestion": _FIX[cat],
        "source": "tool",
    }


def demo() -> None:
    assert classify("MIT") == "permissive"
    assert classify("GPL-3.0-or-later") == "copyleft"
    assert classify("LGPL-2.1") == "weak_copyleft"
    assert classify("WTFPL") == "unknown"

    lic, fam = project_license({"package.json": '{"name":"x","license":"MIT"}'})
    assert (lic, fam) == ("MIT", "permissive"), (lic, fam)
    assert project_license({"LICENSE": "GNU GENERAL PUBLIC LICENSE\nVersion 3"})[1] == "copyleft"
    assert project_license({"pyproject.toml": '[project]\nlicense = "Apache-2.0"\n'})[1] == "permissive"
    assert project_license({"README.md": "hi"}) == ("", "unknown")

    src = (
        "# SPDX-License-Identifier: GPL-3.0-only\n"          # 1
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def f(user):\n"
        "    log.info('user email=%s', user.email)\n"        # 5: pii in log
        "    log.info('user id=%s', user.id)\n"              # 6: benign
        "    url = 'https://x/y?email=' + user.email\n"      # 7: pii in url
        "    return datetime.utcnow()\n"                     # 8: deprecated
    )
    got = {f["line_start"]: (f["category"], f["severity"])
           for f in scan_file("a.py", src, range(1, 9), project_family="permissive")}
    assert got.get(1) == ("license_conflict", "high"), got
    assert got.get(5) == ("pii_exposure", "high"), got
    assert 6 not in got, f"a log line without PII must not fire: {got}"
    assert got.get(7) == ("pii_exposure", "high"), got
    assert got.get(8) == ("deprecated_api", "low"), got

    # copyleft project: vendoring copyleft code is a decision, not a conflict
    assert scan_file("a.py", src, [1], project_family="copyleft")[0]["severity"] == "medium"

    # only ADDED lines are reported
    assert [f["line_start"] for f in scan_file("a.py", src, [6, 8])] == [8]

    # a manifest relicensing itself — the notice regex never sees this line
    manifest = '{\n  "name": "x",\n  "license": "GPL-3.0-only",\n  "version": "1.0.0"\n}\n'
    got_m = {f["line_start"]: (f["category"], f["severity"])
             for f in scan_file("package.json", manifest, range(1, 6))}
    assert got_m.get(3) == ("license_conflict", "high"), got_m
    assert 2 not in got_m and 4 not in got_m, got_m
    mit = '{\n  "license": "MIT"\n}\n'
    assert scan_file("package.json", mit, [2]) == []
    lgpl = '{\n  "license": "LGPL-2.1"\n}\n'
    assert scan_file("package.json", lgpl, [2])[0]["severity"] == "medium"

    js = "console.log(`card_number=${c.card_number}`)\nconst b = new Buffer('x')\n"
    cats = [f["category"] for f in scan_file("a.js", js, [1, 2])]
    assert cats == ["pii_exposure", "deprecated_api"], cats

    print("lib/license_rules.py OK")


if __name__ == "__main__":
    demo()
