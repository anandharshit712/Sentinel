"""A8: lib/triage + review_planner + review_digest (adaptive security fan-out). Logic-only, no LLM/DB."""
from coded_tools.sentinel.review_planner_tool import ReviewPlannerTool, _partition
from coded_tools.sentinel.review_digest_tool import ReviewDigestTool
from lib import contracts, triage


def _profile(files, sensitive=()):
    """files: {path: [added source lines]}. sensitive: iterable of flagged paths."""
    return {
        "files": [{"path": p, "hunks": [{"new_start": 1,
                   "patch": "@@\n" + "".join("+" + ln + "\n" for ln in lines)}]}
                  for p, lines in files.items()],
        "sensitive_flags": ([{"flag": "auth", "files": list(sensitive)}] if sensitive else []),
    }


# ---- triage ----
def test_exclusion_globs():
    assert triage.is_excluded("node_modules/x/index.js")
    assert triage.is_excluded("app/bundle.min.js")
    assert triage.is_excluded("yarn.lock")
    assert not triage.is_excluded("src/app/payments.py")
    assert triage.is_excluded("gen/x.py", extra_globs=["gen/*"]) or triage.is_excluded("gen/x.py", extra_globs=["**/gen/**"])


def test_every_sink_rule_fires_and_benign_does_not():
    for line in ["eval(x)", "subprocess.run(c, shell=True)", "os.system(c)",
                 "q = \"SELECT a FROM t WHERE n = '\" + u + \"'\"",
                 "cur.execute(f\"SELECT {x}\")", "pickle.loads(b)", "yaml.load(f)",
                 "el.innerHTML = x", "hashlib.md5(p)", "requests.get(u, verify=False)"]:
        assert triage.scan_sink(line), line
    for line in ["return a - b", "x = a + b", "def subtract(a, b):", "logger.info('ok')"]:
        assert triage.scan_sink(line) is None, line


def test_rank_is_deterministic_and_prioritizes_sinks():
    prof = _profile({"auth/x.py": ["import os", "os.system(cmd)", "y = 1"]}, sensitive=["auth/x.py"])
    lines = list(triage.iter_added_lines(prof))
    r1 = triage.rank(iter(lines), {"auth/x.py"}, 10)
    r2 = triage.rank(iter(lines), {"auth/x.py"}, 10)
    assert [d["line"] for d in r1] == [d["line"] for d in r2]
    assert "command_injection" in r1[0]["why"]
    assert r1[-1]["code"].strip() == "import os"


# ---- review_planner sizing ----
def _plan(files, repo="x", base="abc123", sensitive=()):
    sly = {"run_id": "t", "event": {"repo": {"name": repo}, "change": {"base_sha": base}},
           "change_profile": _profile(files, sensitive)}
    out = ReviewPlannerTool().invoke({}, sly)
    return out, sly


def test_small_change_is_one_shard_pr_mode():
    out, sly = _plan({"calc.py": ["def sub(a, b):", "    return a - b"]})
    assert out["shard_count"] == 1 and out["mode"] == "pr"
    assert out["agents_to_invoke"] == ["security_reviewer_1"]
    assert contracts.is_valid("review_plan", sly["review_plan"])


def test_empty_tree_base_is_audit_mode():
    out, _ = _plan({"a.py": ["x = 1"]}, base=triage.EMPTY_TREE)
    assert out["mode"] == "audit"


def test_budget_forces_multiple_shards_capped_at_four():
    # tiny budget (audit-smoke=5) + many sink lines across many files -> multi-shard, capped at 4
    sinks = ["os.system(c)", "eval(c)", "pickle.loads(b)", "hashlib.md5(p)"]
    files = {f"m{i}/svc.py": sinks for i in range(8)}
    out, sly = _plan(files, repo="audit-smoke")
    assert 2 <= out["shard_count"] <= 4
    assert out["agents_to_invoke"] == [f"security_reviewer_{i}" for i in range(1, out["shard_count"] + 1)]
    # every in-scope file assigned exactly once across shards
    assigned = [f for s in sly["review_plan"]["shards"] for f in s["files"]]
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == {f"m{i}/svc.py" for i in range(8)}


def test_partition_balances_and_assigns_each_file_once():
    w = {f"mod{i}/f.py": float(i + 1) for i in range(7)}
    parts = _partition(w, 3)
    assert _partition(w, 3) == parts  # deterministic
    flat = [f for b in parts for f in b]
    assert sorted(flat) == sorted(w) and len(flat) == len(set(flat))
    assert all(b for b in parts)  # no empty bin when files >= shards
    loads = [sum(w[f] for f in b) for b in parts]
    assert max(loads) - min(loads) <= max(w.values())  # reasonably balanced


# ---- review_digest ----
def test_digest_merges_shard_keys_sorts_and_caps():
    sly = {"run_id": "t",
           "security_findings_shard_1": {"findings": [
               {"id": "SEC1-001", "severity": "low", "file": "a.py", "title": "x"},
               {"id": "SEC1-002", "severity": "critical", "file": "a.py", "title": "sqli"}]},
           "security_findings_shard_2": {"findings": [
               {"id": "SEC2-001", "severity": "high", "file": "b.py", "title": "cmd"}]}}
    out = ReviewDigestTool().invoke({}, sly)
    assert out["total"] == 3
    assert out["counts"] == {"critical": 1, "high": 1, "medium": 0, "low": 1}
    assert out["findings"][0]["severity"] == "critical"  # sorted worst-first
    assert len(out["findings"]) <= 80


def test_digest_caps_at_80():
    many = [{"id": f"S{i}", "severity": "low", "file": "a.py", "title": "t"} for i in range(120)]
    out = ReviewDigestTool().invoke({}, {"run_id": "t", "security_findings_shard_1": {"findings": many}})
    assert out["total"] == 120 and len(out["findings"]) == 80
