"""Phase 7 (12 §8): dependency licence resolution and the spec_check invariants. No network, no DB.

Registry access is always injected (a fake fetcher, or `license_rules._http_json` patched), and
the cache always points at tmp_path, so these tests say the same thing offline and online.
"""
import json
import os
import urllib.error

import pytest

from coded_tools.sentinel.dependency_cve_tool import _added_deps
from coded_tools.sentinel.license_scanner_tool import LicenseScannerTool
from lib import license_rules, spec_check
from lib.license_rules import classify, dependency_findings, resolve_licenses


def _profile(path, lines, start=1):
    patch = "@@\n" + "".join("+" + s + "\n" for s in lines)
    return {"files": [{"path": path, "hunks": [{"new_start": start, "patch": patch}]}]}


def _registry(table):
    """A fake fetcher: {name: licence | Exception}. Records every URL it is asked for."""
    calls = []

    def fetch(url):
        calls.append(url)
        for name, val in table.items():
            if f"/{name}/" in url or url.endswith(f"/{name}/json"):
                if isinstance(val, Exception):
                    raise val
                return {"license": val} if "npmjs" in url else {"info": {"license": val}}
        raise urllib.error.HTTPError(url, 404, "not found", None, None)
    fetch.calls = calls
    return fetch


# ---------------------------------------------------------------- classify
@pytest.mark.parametrize("text,family", [
    ("MIT", "permissive"), ("Apache-2.0", "permissive"), ("BSD License", "permissive"),
    ("GPL v3", "copyleft"), ("GPLv2+", "copyleft"), ("AGPL-3.0-only", "copyleft"), ("SSPL-1.0", "copyleft"),
    ("LGPL-2.1", "weak_copyleft"), ("GNU Lesser General Public License v3 (LGPLv3)", "weak_copyleft"),
    ("MPL-2.0", "weak_copyleft"),
    ("MIT OR GPL-2.0", "permissive"),          # dual licence: the consumer may choose
    ("Commercial", "unknown"), ("not permitted", "unknown"), ("", "unknown"),
])
def test_classify(text, family):
    assert classify(text) == family


# ---------------------------------------------------------------- resolve_licenses
def test_resolves_from_both_registries_and_caches(tmp_path):
    fetch = _registry({"pyqt5": "GPL v3", "react": "MIT"})
    rows = resolve_licenses([("pypi", "pyqt5"), ("npm", "react")], fetch=fetch, cache_dir=str(tmp_path))
    assert [(r["name"], r["family"], r["status"]) for r in rows] == [
        ("pyqt5", "copyleft", "resolved"), ("react", "permissive", "resolved")]
    assert any("pypi.org/pypi/pyqt5/json" in u for u in fetch.calls)
    assert any("registry.npmjs.org/react/latest" in u for u in fetch.calls)

    again = _registry({})                       # registry now empty: answers must come from cache
    rows2 = resolve_licenses([("pypi", "pyqt5"), ("npm", "react")], fetch=again, cache_dir=str(tmp_path))
    assert again.calls == [] and [r["status"] for r in rows2] == ["resolved", "resolved"]


def test_cache_expires_after_seven_days(tmp_path):
    resolve_licenses([("pypi", "x")], fetch=_registry({"x": "MIT"}), cache_dir=str(tmp_path))
    later = _registry({"x": "GPL-3.0"})
    rows = resolve_licenses([("pypi", "x")], fetch=later, cache_dir=str(tmp_path),
                            now=os.path.getmtime(tmp_path / "pypi" / "x.json") + 8 * 24 * 3600)
    assert len(later.calls) == 1 and rows[0]["family"] == "copyleft"


def test_offline_is_unresolved_and_pays_one_timeout_not_thirty(tmp_path):
    fetch = _registry({n: urllib.error.URLError("offline") for n in ("a", "b", "c")})
    rows = resolve_licenses([("pypi", "a"), ("pypi", "b"), ("npm", "c")], fetch=fetch,
                            cache_dir=str(tmp_path))
    assert len(fetch.calls) == 1
    assert all(r["status"] == "unresolved" and r["family"] == "unknown" for r in rows)
    assert "unreachable" in rows[0]["reason"] and "earlier in this run" in rows[1]["reason"]
    assert dependency_findings(rows, "requirements.txt") == []      # unresolved is not a finding...
    assert not list((tmp_path).rglob("*.json"))                     # ...and is never cached


def test_404_unknown_licence_and_budget_are_unresolved(tmp_path):
    fetch = _registry({"weird": "Proprietary EULA", "ok": "MIT"})
    rows = resolve_licenses([("pypi", "missing"), ("pypi", "weird"), ("pypi", "ok"), ("pypi", "late")],
                            fetch=fetch, cache_dir=str(tmp_path), max_lookups=3)
    by = {r["name"]: r for r in rows}
    assert "HTTP 404" in by["missing"]["reason"]                     # a 404 does not stop the run
    assert by["weird"]["status"] == "unresolved" and "not recognised" in by["weird"]["reason"]
    assert by["ok"]["status"] == "resolved"
    assert "budget" in by["late"]["reason"] and len(fetch.calls) == 3


def test_lookups_off_never_touches_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_LICENSE_LOOKUP", "off")
    monkeypatch.setattr(license_rules, "_http_json", lambda url: pytest.fail("network used"))
    rows = resolve_licenses([("npm", "react")], cache_dir=str(tmp_path))
    assert rows[0]["status"] == "unresolved" and "off" in rows[0]["reason"]


def test_registry_document_shapes():
    assert license_rules._license_from("npm", {"license": {"type": "MIT"}}) == "MIT"   # old npm form
    info = {"license": "Copyright ...\nfull licence text", "license_expression": None,
            "classifiers": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"]}
    assert classify(license_rules._license_from("pypi", {"info": info})) == "copyleft"
    assert license_rules._license_from("pypi", {"info": {"license_expression": "MIT"}}) == "MIT"


def test_findings_are_aggregated_and_capped_at_medium():
    rows = [{"name": f"g{i}", "license": "GPL-3.0", "family": "copyleft", "status": "resolved"}
            for i in range(20)]
    rows.append({"name": "l", "license": "LGPL-2.1", "family": "weak_copyleft", "status": "resolved"})
    out = dependency_findings(rows, "package.json")
    # one finding per family, however many packages: a licence alone can never fail the gate
    assert [(f["severity"], f["source"]) for f in out] == [("medium", "tool"), ("low", "tool")]
    assert "20 added dependencies" in out[0]["title"]


# ---------------------------------------------------------------- the tool
def test_added_deps_skips_pip_options_and_package_metadata():
    req = _added_deps(_profile("requirements-dev.txt", ["-r base.txt", "--index-url x", "pyqt5==5.15"]))
    assert [d[0] for d in req] == ["pyqt5"]
    pkg = _added_deps(_profile("package.json", ['"version": "1.0.0",', '"react": "^18.2.0"']))
    assert [d[0] for d in pkg] == ["react"]


def test_tool_reports_resolved_and_unresolved(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_LICENSE_LOOKUP", "on")
    monkeypatch.setattr(license_rules, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(license_rules, "_http_json", _registry({"pyqt5": "GPL v3", "requests": "Apache-2.0"}))
    prof = _profile("requirements.txt", ["pyqt5==5.15.10", "requests>=2.31", "ghost-pkg==1.0"])
    out = LicenseScannerTool().invoke({}, {"run_id": "t", "change_profile": prof})
    assert [(f["category"], f["severity"]) for f in out["findings"]] == [("dependency_license", "medium")]
    status = {r["name"]: r["status"] for r in out["dependency_licenses"]}
    assert status == {"pyqt5": "resolved", "requests": "resolved", "ghost-pkg": "unresolved"}
    assert out["note"].startswith("2 of 3") and "UNRESOLVED" in out["note"] and "NOT clean" in out["note"]


def test_tool_reads_pyproject_dependencies_from_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_LICENSE_LOOKUP", "on")
    monkeypatch.setattr(license_rules, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(license_rules, "_http_json", _registry({"httpx": "BSD-3-Clause"}))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nkeywords = [\n  "cli",\n]\ndependencies = [\n  "httpx>=0.27",\n]\n')
    # both added lines look like PEP 508 strings; only the one in `dependencies` is a dependency
    prof = _profile("pyproject.toml", ['  "cli",', '  "httpx>=0.27",'], start=4)
    out = LicenseScannerTool().invoke({}, {"run_id": "t", "change_profile": prof,
                                           "repo_workspace": str(tmp_path)})
    assert [(r["name"], r["status"]) for r in out["dependency_licenses"]] == [("httpx", "resolved")]


# ---------------------------------------------------------------- spec_check invariants
def _kinds(src, fn):
    return [m["kind"] for m in spec_check.check_function(src, fn)]


def test_bounds_fire_only_on_an_undocumented_out_of_range_literal():
    src = ('def score(x):\n    """Returns a score between 0 and 100."""\n'
           '    if x is None:\n        return -1\n    return min(100, x)\n')
    assert _kinds(src, "score") == ["documented_bound_violation"]
    m = spec_check.check_function(src, "score")[0]
    assert m["code_returns"] == [-1.0] and "stale" in m["caveat"]
    # the docstring names the sentinel itself: a documented exception
    assert _kinds(src.replace('100."""', '100, or -1 if x is None."""'), "score") == []
    # an input-domain range is not a claim about the result
    assert _kinds('def ap(n):\n    """For numbers 0-9, spell it out."""\n    return -1\n', "ap") == []
    # computed values are never judged
    assert _kinds('def p(a):\n    """Returns a value between 0 and 1."""\n    return a * 2\n', "p") == []
    nn = 'def d(a, b):\n    """Returns a non-negative distance."""\n    return -1 if a is None else abs(a - b)\n'
    assert _kinds(nn, "d") == []      # IfExp, not a bare literal return: silence


def test_idempotence_fires_only_on_unguarded_outside_state():
    src = ('class R:\n    def add(self, x):\n        """Idempotent: safe to call twice."""\n'
           '        self.count += 1\n        self.items.append(x)\n')
    found = spec_check.check_function(src, "add")
    assert [m["kind"] for m in found] == ["documented_idempotence_violation"]
    assert found[0]["code_accumulates"] == ["self.count += 1", "self.items.append(x)"]
    guarded = src.replace("        self.count += 1\n        self.items",
                          "        if x not in self.items:\n            self.items")
    assert _kinds(guarded, "add") == []
    local = 'def norm(xs):\n    """Idempotent."""\n    out = []\n    out.append(1)\n    return out\n'
    assert _kinds(local, "norm") == []                    # a list this call created does not escape
    assert _kinds(src.replace("Idempotent: safe to call twice.", "Adds x."), "add") == []   # no claim


def test_return_type_mismatch():
    assert _kinds("def f(x) -> str:\n    return 0\n", "f") == ["return_type_mismatch"]
    assert _kinds("def f(x) -> int:\n    if x:\n        return\n    return 1\n", "f") == ["return_type_mismatch"]
    for ok in ("def f(x) -> float:\n    return 0\n",              # numeric tower
               "def f(x) -> Optional[str]:\n    return None\n",
               "def f(x) -> 'str | None':\n    return None\n",
               "def f(x) -> list[int]:\n    return []\n",
               "def f(x) -> Iterator[int]:\n    yield 1\n",           # unknown name: silent
               "def f(x) -> str:\n    yield 'a'\n",                  # generator
               "def f(x) -> str:\n    def g():\n        return 0\n    return 'a'\n",   # nested def
               'def f(x) -> str:\n    """f(None) is None."""\n    return None\n',      # documented
               "def f(x) -> MyType:\n    return 0\n"):
        assert _kinds(ok, "f") == [], ok


def test_check_node_covers_every_method_and_keeps_the_rate_check():
    src = ("class A:\n    def f(self) -> str:\n        return 'ok'\n"
           "class B:\n    def f(self) -> str:\n        return 1\n")
    import ast
    fires = [m for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
             for m in spec_check.check_node(n)]
    assert len(fires) == 1                                  # check_function would see only A.f
    spec_check.demo()                                       # the documented-rate cases still hold
    assert json.dumps(fires)                                # findings stay JSON-serialisable
