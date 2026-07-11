"""A2 security tools: secret_scanner + dependency_cve over synthetic diff hunks."""
from coded_tools.sentinel.secret_scanner_tool import SecretScannerTool
from coded_tools.sentinel.dependency_cve_tool import DependencyCveTool


def _profile(path, patch):
    return {"change_profile": {"files": [{"path": path, "hunks": [{"new_start": 1, "patch": patch}]}]}}


def test_secret_scanner_flags_each_secret_type_and_ignores_benign():
    patch = (
        "@@ -0,0 +1,6 @@\n"
        '+AWS_KEY = "AKIA1234567890ABCDEF"\n'
        "+-----BEGIN RSA PRIVATE KEY-----\n"
        '+token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEFghiJKLmnoPQ"\n'
        '+password = "supersecret123"\n'
        '+cache_key = "Xk9dP2mQ7rZ1vL8wT4nB6yH3cF5gJ0aS"\n'
        "+benign = compute(x, y)\n"
    )
    out = SecretScannerTool().invoke({}, _profile("app/config.py", patch))
    findings = out["findings"]
    assert len(findings) == 5, [f["category"] for f in findings]
    assert all(f["severity"] == "critical" for f in findings)  # secrets always critical
    cats = {f["category"] for f in findings}
    assert cats == {"aws_access_key", "private_key", "jwt", "hardcoded_credential", "high_entropy_secret"}
    assert all(f["source"] == "tool" and f["file"] == "app/config.py" for f in findings)
    assert len(out["added_lines"]) == 6  # all added lines returned for the LLM to review


def test_secret_scanner_only_scans_added_lines():
    patch = "@@ -1,1 +1,1 @@\n" '-old = "AKIA0000000000000000"\n' "+clean = 1\n"
    out = SecretScannerTool().invoke({}, _profile("x.py", patch))
    assert out["findings"] == []  # the AKIA line is removed (-), not added


def test_dependency_cve_flags_vulnerable_added_deps():
    patch = (
        "@@ -0,0 +1,3 @@\n"
        "+vulnerable-demo-lib==1.0.0\n"
        "+requests==2.20.0\n"
        "+safe-lib==1.0.0\n"
    )
    out = DependencyCveTool().invoke({}, _profile("requirements.txt", patch))
    ids = {f["title"].split(": ")[1] for f in out["findings"]}
    assert ids == {"CVE-2099-0001", "CVE-2023-32681"}
    sev = {f["category"] for f in out["findings"]}
    assert sev == {"dependency_cve"}


def test_dependency_cve_respects_fixed_version():
    patch = "@@ -0,0 +1,1 @@\n+requests==2.31.0\n"  # fixed version, not affected (<2.31.0)
    out = DependencyCveTool().invoke({}, _profile("requirements.txt", patch))
    assert out["findings"] == []


# ---- adaptive fan-out: dangerous-sink findings + shard filtering ----
def test_secret_scanner_emits_deterministic_sink_findings():
    patch = ("@@ -0,0 +1,3 @@\n"
             "+q = \"SELECT id FROM users WHERE n = '\" + u + \"'\"\n"
             "+data = pickle.loads(blob)\n"
             "+clean = a + b\n")
    out = SecretScannerTool().invoke({}, _profile("app/svc.py", patch))
    cats = {f["category"] for f in out["findings"]}
    assert "sql_injection" in cats and "unsafe_deserialization" in cats
    assert all(f["source"] == "tool" for f in out["findings"])


def _plan_sly(shard_files_map, files_map):
    """Build sly_data with a review_plan + a change_profile covering files_map {path: patch}."""
    plan = {"shards": [{"shard": n, "label": "s", "files": fs} for n, fs in shard_files_map.items()],
            "mode": "audit", "metrics": {"shard_count": len(shard_files_map)}}
    profile = {"files": [{"path": p, "hunks": [{"new_start": 1, "patch": pt}]}
                         for p, pt in files_map.items()], "sensitive_flags": []}
    return {"run_id": "r", "review_plan": plan, "change_profile": profile,
            "event": {"repo": {"name": "audit-smoke"}}}


def test_secret_scanner_restricts_to_shard_files_and_records_coverage():
    sly = _plan_sly(
        {1: ["a.py"], 2: ["b.py"]},
        {"a.py": "@@ -0,0 +1,1 @@\n+AWS = \"AKIA1234567890ABCDEF\"\n",
         "b.py": "@@ -0,0 +1,1 @@\n+other = \"AKIA0000000000000000\"\n"})
    out = SecretScannerTool().invoke({"shard": 1}, sly)
    assert all(f["file"] == "a.py" for f in out["findings"])  # shard 2's b.py not scanned
    assert out["findings"] and out["findings"][0]["severity"] == "critical"
    assert "review_snippets" in out and "coverage" in out
    assert sly["review_coverage"]["1"]["shard"] == 1


def test_secret_scanner_shard_not_in_plan_is_harmless():
    sly = _plan_sly({1: ["a.py"]}, {"a.py": "@@ -0,0 +1,1 @@\n+x = 1\n"})
    out = SecretScannerTool().invoke({"shard": 3}, sly)  # only shard 1 exists
    assert out["findings"] == [] and out["review_snippets"] == [] and "note" in out


def test_secret_scanner_no_plan_keeps_legacy_shape():
    patch = "@@ -0,0 +1,2 @@\n+AWS = \"AKIA1234567890ABCDEF\"\n+ok = 1\n"
    out = SecretScannerTool().invoke({}, _profile("x.py", patch))  # no review_plan
    assert "added_lines" in out and "findings" in out  # legacy keys preserved
    assert any(f["category"] == "aws_access_key" for f in out["findings"])
