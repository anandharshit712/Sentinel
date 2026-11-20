"""Deterministic compliance floor: licensing, PII exposure, deprecated APIs (08 §3 P2.3).

The floor for `compliance_review_agent`, same contract as every other floor in this project: it
fires from code, so a drifting reviewer cannot produce a clean compliance report (rule 4).

Checked offline:

  * the project's own declared licence (manifest field or LICENSE file),
  * copyleft notices in the changed files themselves — vendored GPL/AGPL source, which is the
    licensing accident that actually reaches a diff,
  * a manifest licence field changed to something copyleft,
  * PII written to logs or URLs,
  * deprecated APIs.

Checked online (`resolve_licenses`, 12 §8): the licence of each dependency the diff ADDS, from the
PyPI JSON API or the npm registry (latest release — the range in the manifest is not solved).
Lookups are bounded (`MAX_LOOKUPS` per call, `LOOKUP_TIMEOUT` s each, the first unreachable registry
stops the rest) and cached on disk for `CACHE_TTL`. Anything not positively classified — offline,
registry error, budget spent, or a licence string this module does not recognise — is `unresolved`,
which is never the same as clean. `SENTINEL_LICENSE_LOOKUP=off` keeps every lookup offline (cache
only) for air-gapped installs and for the test suite.

`licenses_seen` is reported so the report says what was actually inspected.

    python -m lib.license_rules        # self-check
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# SPDX-ish families, plus the spellings PyPI classifiers and free-text `license` fields use.
# Not a full SPDX matrix: anything unmatched is `unknown`, which callers report as unresolved.
COPYLEFT = ("agpl", "gpl", "gnu general public license", "gnu affero", "sspl", "eupl",
            "cc-by-sa", "cc by-sa")
WEAK_COPYLEFT = ("lgpl", "lesser general public", "library general public", "mpl", "mozilla public",
                 "epl", "eclipse public")
PERMISSIVE = ("mit", "apache", "bsd", "isc", "unlicense", "0bsd", "python software foundation",
              "psf", "zlib", "cc0")

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


_FAMILIES = [(name, re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in toks)
                                + r")(?:v?\d[\w.+]*)?(?![a-z])"))
             # Weak copyleft is tested FIRST: "GNU Lesser General Public License" is weak, and the
             # broader family would otherwise report an LGPL dependency as a hard conflict.
             for name, toks in (("weak_copyleft", WEAK_COPYLEFT), ("copyleft", COPYLEFT),
                                ("permissive", PERMISSIVE))]
_RANK = {"permissive": 0, "weak_copyleft": 1, "copyleft": 2, "unknown": 3}


def classify(text: str) -> str:
    """'copyleft' | 'weak_copyleft' | 'permissive' | 'unknown' for a licence string or notice.

    Tokens match whole words only, so "MIT" is found but "permitted" is not. A dual licence
    ("MIT OR GPL-2.0") takes its most permissive recognised alternative — the consumer may choose.
    """
    low = text.lower()
    # Only a short expression is split: licence prose says "express or implied".
    alts = re.split(r"\s+or\s+", low.strip("() ")) if len(low) <= 120 else [low]
    found = []
    for alt in alts:
        found.append(next((name for name, rx in _FAMILIES if rx.search(alt)), "unknown"))
    return min(found, key=_RANK.__getitem__)


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


# ---- dependency licence resolution (registry lookups) ----------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".cache", "licenses")      # gitignored
CACHE_TTL = 7 * 24 * 3600
LOOKUP_TIMEOUT = 5
MAX_LOOKUPS = 30


def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "sentinel-license-check"})
    with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT) as resp:
        return json.load(resp)


def _license_from(ecosystem: str, doc: Any) -> str:
    """The licence string a registry document declares, '' if it declares none."""
    if not isinstance(doc, dict):
        return ""
    if ecosystem == "npm":
        lic = doc.get("license")
        if isinstance(lic, dict):          # old packages: {"type": "MIT", "url": ...}
            lic = lic.get("type")
        return lic.strip() if isinstance(lic, str) else ""
    info = doc.get("info") or {}
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr
    # A classifier is a controlled vocabulary; the free-text `license` field is sometimes the whole
    # licence text, so it comes last and only its first line is kept.
    trove = [c.split("::")[-1].strip() for c in info.get("classifiers") or []
             if c.startswith("License ::") and c.count("::") >= 2]
    if trove:
        return " OR ".join(trove)
    lines = (info.get("license") or "").strip().splitlines()
    return lines[0][:120] if lines else ""


def _registry_url(ecosystem: str, name: str) -> str:
    if ecosystem == "npm":
        return f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/latest"
    return f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/json"


def resolve_licenses(deps: Iterable[Tuple[str, str]], fetch: Optional[Callable[[str], Any]] = None,
                     cache_dir: Optional[str] = None, max_lookups: int = MAX_LOOKUPS,
                     now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Licence of each (ecosystem, name) dependency: {ecosystem, name, license, family, status, reason}.

    status is `resolved` only when the licence was found AND classified; everything else is
    `unresolved` with the reason, because "we could not tell" must never read as "clean".
    """
    now = time.time() if now is None else now
    cache_dir = cache_dir or CACHE_DIR
    online = fetch is not None or os.environ.get("SENTINEL_LICENSE_LOOKUP", "on").lower() != "off"
    fetch = fetch or _http_json
    budget, unreachable = max_lookups, ""
    out: List[Dict[str, Any]] = []
    for eco, name in dict.fromkeys(deps):          # de-duplicated, order kept
        row: Dict[str, Any] = {"ecosystem": eco, "name": name, "license": "", "family": "unknown",
                               "status": "unresolved", "reason": ""}
        out.append(row)
        path = os.path.join(cache_dir, eco, urllib.parse.quote(name, safe="") + ".json")
        lic: Optional[str] = None
        try:
            if now - os.path.getmtime(path) < CACHE_TTL:
                with open(path, encoding="utf-8") as fh:
                    lic = json.load(fh)["license"]
        except (OSError, ValueError, KeyError):
            pass
        if lic is None:
            if not online:
                row["reason"] = "registry lookups are off (SENTINEL_LICENSE_LOOKUP=off)"
                continue
            if unreachable:
                row["reason"] = f"registry unreachable earlier in this run ({unreachable})"
                continue
            if budget <= 0:
                row["reason"] = f"lookup budget of {max_lookups} per run spent"
                continue
            budget -= 1
            try:
                lic = _license_from(eco, fetch(_registry_url(eco, name)))
            except urllib.error.HTTPError as e:
                row["reason"] = f"registry answered HTTP {e.code}"
                continue
            except Exception as e:   # offline, DNS, timeout: stop paying a timeout per dependency
                unreachable = type(e).__name__
                row["reason"] = f"registry unreachable ({unreachable})"
                continue
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump({"license": lic}, fh)
                os.replace(path + ".tmp", path)
            except OSError:
                pass                 # a cache that cannot be written costs a lookup, nothing more
        row["license"] = lic
        row["family"] = classify(lic) if lic else "unknown"
        if row["family"] == "unknown":
            row["reason"] = (f"licence {lic!r} not recognised" if lic
                             else "the registry declares no licence")
        else:
            row["status"] = "resolved"
    return out


def dependency_findings(rows: List[Dict[str, Any]], manifest: str) -> List[Dict[str, Any]]:
    """At most one finding per family over all resolved dependencies, capped at `medium`.

    One aggregated finding — not one per package — so licence results alone can never push a run
    out of the low risk band (risk-v1: one medium = 5 points + 0.6 health); a copyleft dependency is
    a decision for a human, not a gate failure.
    """
    out = []
    for fam, sev in (("copyleft", "medium"), ("weak_copyleft", "low")):
        hits = [r for r in rows if r["status"] == "resolved" and r["family"] == fam]
        if not hits:
            continue
        names = ", ".join(f"{r['name']} ({r['license']})" for r in hits)
        label = fam.replace("_", " ")
        out.append({
            "id": f"COMP-dependency_license-{fam}",
            "category": "dependency_license",
            "severity": sev,
            "file": manifest,
            "title": f"{len(hits)} added dependenc{'y' if len(hits) == 1 else 'ies'} under a "
                     f"{label} licence",
            "explanation": f"Registry-declared licence (latest release): {names}. Shipping these "
                           f"can oblige the project to release its own source under {label} terms.",
            "fix_suggestion": _FIX["dependency_license"],
            "source": "tool",
        })
    return out


_FIX = {
    "license_conflict": "Remove the copyleft code, replace it with a permissively licensed "
                        "equivalent, or take a licence decision before merging.",
    "dependency_license": "Confirm the licence is acceptable for how this project is distributed, "
                          "or pick a permissively licensed alternative.",
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
    assert classify("GPL v3") == "copyleft"                              # PyQt5's PyPI field
    assert classify("GNU Lesser General Public License v3 (LGPLv3)") == "weak_copyleft"
    assert classify("Apache Software License") == "permissive"           # a trove classifier
    assert classify("MIT OR GPL-2.0") == "permissive"                    # dual: consumer chooses
    assert classify("all rights reserved, use not permitted") == "unknown"   # "permit" is not MIT

    import tempfile
    rows = resolve_licenses([("pypi", "pyqt5"), ("npm", "left-pad")], cache_dir=tempfile.mkdtemp(),
                            fetch=lambda url: {"info": {"license": "GPL v3"}} if "pypi" in url
                            else (_ for _ in ()).throw(OSError("offline")))
    assert [(r["family"], r["status"]) for r in rows] == [("copyleft", "resolved"),
                                                         ("unknown", "unresolved")], rows
    assert [f["severity"] for f in dependency_findings(rows, "requirements.txt")] == ["medium"]

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
