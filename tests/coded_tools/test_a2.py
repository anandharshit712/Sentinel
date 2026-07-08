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
