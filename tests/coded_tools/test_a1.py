"""A1 change-analysis tools: git_diff -> ast_analyzer -> dependency_graph over a golden diff.

Builds a throwaway git repo shaped like python-payments-service with a base and a head commit
that exercises modified / added / deleted / renamed / binary edge cases, then runs the three
tools in pipeline order and asserts the assembled, validated ChangeProfile.
"""
import subprocess

import pytest

from coded_tools.sentinel.git_diff_tool import GitDiffTool
from coded_tools.sentinel.ast_analyzer_tool import AstAnalyzerTool
from coded_tools.sentinel.dependency_graph_tool import DependencyGraphTool
from lib import contracts


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _write(repo, rel, content):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)


def _rev(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "python-payments-service"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")

    _write(r, "app/__init__.py", "")
    _write(r, "app/auth/__init__.py", "")
    _write(r, "app/auth/login.py", "def authenticate(user, pw):\n    return user == 'a'\n")
    _write(r, "app/api.py", "from app.auth.login import authenticate\n\n"
                            "def login_route(user, pw):\n    return authenticate(user, pw)\n")
    _write(r, "app/legacy.py", "def old():\n    return 1\n")
    _write(r, "app/util_old.py", "def helper():\n    return 2\n")
    _write(r, "data.bin", b"\x00\x01\x02BASE\x00")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base = _rev(r)

    # head changes
    _write(r, "app/auth/login.py",
           "def authenticate(user, pw):\n    return user == 'a' and pw == 'b'\n\n"
           "def check_token(token):\n    return bool(token)\n")
    _write(r, "app/audit.py", "def record(x):\n    return x\n")
    (r / "app/legacy.py").unlink()
    (r / "app/util_old.py").rename(r / "app/util.py")
    _write(r, "data.bin", b"\x00\x01\x02HEAD-CHANGED\x00\x09")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "head")
    head = _rev(r)
    return r, base, head


def _run_pipeline(repo, base, head):
    sly = {"run_id": "test-run"}
    args = {"repo_path": str(repo), "base_ref": base, "head_ref": head,
            "repo_name": "python-payments-service"}
    assert not isinstance(GitDiffTool().invoke(args, sly), str), "git_diff errored"
    assert not isinstance(AstAnalyzerTool().invoke(args, sly), str), "ast errored"
    out = DependencyGraphTool().invoke(args, sly)
    assert not isinstance(out, str), f"dependency_graph errored: {out}"
    return sly


def test_git_diff_change_types_and_binary(repo):
    r, base, head = repo
    sly = {"run_id": "x"}
    GitDiffTool().invoke({"repo_path": str(r), "base_ref": base, "head_ref": head}, sly)
    files = {f["path"]: f for f in sly["change_profile_wip"]["files"]}
    assert files["app/auth/login.py"]["change_type"] == "modified"
    assert files["app/audit.py"]["change_type"] == "added"
    assert files["app/legacy.py"]["change_type"] == "deleted"
    assert files["app/util.py"]["change_type"] == "renamed"
    assert files["app/util.py"]["old_path"] == "app/util_old.py"
    assert files["data.bin"].get("is_binary") is True
    assert sly["change_profile_wip"]["loc_added"] > 0


def test_ast_finds_changed_and_new_functions(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    cp = sly["change_profile"]
    # check_token is brand new; record is a new file's function; rename introduces no false-new
    assert "app/auth/login.py::check_token" in cp["new_functions"]
    assert "app/audit.py::record" in cp["new_functions"]
    assert "app/util.py::helper" not in cp["new_functions"]  # rename followed, not counted new
    login = next(f for f in cp["files"] if f["path"] == "app/auth/login.py")
    names = {d["name"]: d for d in login["functions_changed"]}
    assert names["authenticate"]["is_new"] is False
    assert names["check_token"]["is_new"] is True


def test_dependency_graph_blast_and_sensitive(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    cp = sly["change_profile"]
    assert "app.api" in cp["blast_radius"]["direct"]  # api imports the changed auth.login
    assert cp["blast_radius"]["count"] >= 1
    flags = {f["flag"] for f in cp["sensitive_flags"]}
    assert "auth" in flags
    auth_flag = next(f for f in cp["sensitive_flags"] if f["flag"] == "auth")
    assert "app/auth/login.py" in auth_flag["files"]


def test_change_profile_is_a_valid_contract(repo):
    r, base, head = repo
    sly = _run_pipeline(r, base, head)
    assert contracts.is_valid("change_profile", sly["change_profile"]), \
        contracts.iter_errors("change_profile", sly["change_profile"])
    assert "change_profile_wip" not in sly  # finalize consumes the WIP key
