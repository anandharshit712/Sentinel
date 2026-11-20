"""Phase 6 gate, JS/TS path: an untested changed function -> a Jest test with a Stryker score.

Needs the Neuro-SAN server running with registries/ (CLAUDE.md "Run servers"), Postgres, node+npm,
and network for `npm install` (the repo's deps, then Stryker --no-save):

    PYTHONPATH=. python scripts/verify_p6_js.py          # one trial
    PYTHONPATH=. python scripts/verify_p6_js.py 3        # three
    NEURO_SAN_PORT=8091 ...                              # a server on another port

Two fixtures, both copies of samples/node-catalog-service with a real base..head commit pair:

  untested  head adds src/catalog/pricing.js:bulkPrice, which no test loads. The deterministic half
            runs in process exactly as the gate does (git_diff -> ast_analyzer -> dependency_graph ->
            test_runner with Jest coverage -> coverage_gap), then lib.untested picks candidates and
            lib.untested.generate runs sentinel_testgen through the Gateway's own invoke_network.
            PASS = a proposal for bulkPrice whose evidence came from Stryker, accepted.
  tested    head changes addItem, which test/catalog.test.js imports, calls and executes.
            PASS = zero candidates and zero model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

from db import dao
from gateway.invoker.neuro_san_client import invoke_network
from lib import sandbox, untested

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "samples", "node-catalog-service")
REPO = "node-catalog-service"          # repo_config: install_deps true (our own fixture)

PRICING = '''"use strict";

/**
 * Price of an order line. Orders of more than 10 units get 10% off; the result is rounded to cents.
 */
function bulkPrice(qty, unit) {
  if (qty > 10) {
    return Math.round(qty * unit * 0.9 * 100) / 100;
  }
  return qty * unit;
}

module.exports = { bulkPrice };
'''


def _git(ws, *a):
    return subprocess.run(["git", "-C", ws, *a], check=True, capture_output=True,
                          text=True).stdout.strip()


def _fixture(kind):
    ws = tempfile.mkdtemp(prefix=f"sentinel-p6js-{kind}-")
    shutil.rmtree(ws)
    shutil.copytree(SAMPLE, ws, ignore=shutil.ignore_patterns("node_modules", ".impeccable"))
    with open(os.path.join(ws, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("node_modules/\ncoverage/\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    base = _git(ws, "rev-parse", "HEAD")
    if kind == "untested":
        with open(os.path.join(ws, "src", "catalog", "pricing.js"), "w", encoding="utf-8") as fh:
            fh.write(PRICING)
    else:
        p = os.path.join(ws, "src", "catalog", "processor.js")
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(src.replace("price: item.price || 0", "price: Number(item.price) || 0"))
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "head")
    return ws, base, _git(ws, "rev-parse", "HEAD")


def _prepare(ws, run_id, base, head):
    """The gate's deterministic half, in process."""
    from coded_tools.sentinel.ast_analyzer_tool import AstAnalyzerTool
    from coded_tools.sentinel.coverage_gap_tool import CoverageGapTool
    from coded_tools.sentinel.dependency_graph_tool import DependencyGraphTool
    from coded_tools.sentinel.git_diff_tool import GitDiffTool
    from coded_tools.sentinel.test_runner_tool import TestRunnerTool

    event = {"event_id": f"p6js-{run_id[:8]}", "source": "github",
             "repo": {"url": "file://x", "name": REPO, "default_branch": "main"},
             "change": {"base_sha": base, "head_sha": head, "branch": "pr-p6",
                        "title": "bulk pricing", "author": "dev",
                        "description": "Adds bulk pricing: orders of more than 10 units get 10% "
                                       "off, rounded to cents."},
             "target_transition": {"from_env": "dev", "to_env": "test"}, "requested_by": "tester"}
    dao.ensure_run(run_id, event)
    sly = {"run_id": run_id, "event": event, "repo_workspace": ws}
    for tool in (GitDiffTool(), AstAnalyzerTool(), DependencyGraphTool()):
        out = tool.invoke({}, sly)
        if isinstance(out, str):
            raise RuntimeError(f"{type(tool).__name__}: {out}")
    sly["test_results"] = TestRunnerTool().invoke({}, sly)
    sly["coverage_gaps"] = CoverageGapTool().invoke({}, sly)
    return sly


def _trial(kind, host, port):
    run_id = str(uuid.uuid4())
    ws, base, head = _fixture(kind)
    sandbox.open_run(run_id, ws, REPO)          # no-op unless SANDBOX_MODE=docker
    notes, ok, calls, agent = [], True, [], None
    try:
        sly = _prepare(ws, run_id, base, head)
        tr, cg = sly["test_results"], sly["coverage_gaps"]
        notes.append(f"jest {tr.get('totals')} coverage_report={'yes' if tr.get('coverage_report') else 'NO'} "
                     f"measured={cg.get('measured')}")
        ok = ok and bool(tr.get("coverage_report")) and cg.get("measured") is True

        sel = untested.candidates_for_run(sly, ws, REPO)   # the exact call the Gateway makes
        notes.append(f"candidates={[c['function'] for c in sel['candidates']]} "
                     f"skipped={[(s['function'], s['reason']) for s in sel['skipped']]}")

        def invoke(extra):
            calls.append(extra)
            nonlocal agent
            agent, _sly, answer = invoke_network(run_id, sly["event"], ws, host=host, port=port,
                                                 network="sentinel_testgen",
                                                 prompt=untested.PROMPT, extra_sly=extra)
            return answer

        gen = untested.generate(sel, invoke)
        notes.append(f"model calls={len(calls)} generate={json.dumps(gen)[:300]}")
    finally:
        sandbox.close_run(run_id)

    if kind == "tested":
        ok = ok and sel["candidates"] == [] and calls == []
        notes.append(f"{'ok ' if ok else 'FAIL'} all changed functions tested -> zero candidates, "
                     f"zero model calls")
    else:
        ok = ok and [c["function"] for c in sel["candidates"]] == ["bulkPrice"]
        props = [p for p in dao.list_test_proposals(run_id) if p["target_function"] == "bulkPrice"]
        notes.append(f"{'ok ' if props else 'MISSING'} proposal recorded ({len(props)})")
        ok = ok and bool(props)
        if props:
            p = props[0]                          # newest first
            ev = p["evaluation"] if isinstance(p["evaluation"], dict) else json.loads(p["evaluation"])
            mut = (ev.get("oracle_evidence") or {}).get("mutation") or {}
            spec = (ev.get("oracle_evidence") or {}).get("specification") or {}
            score = p["mutation_score"] or 0.0
            notes.append(f"{'ok ' if mut.get('engine') == 'stryker' else 'WRONG'} engine="
                         f"{mut.get('engine')} lines={mut.get('lines')}")
            notes.append(f"{'ok ' if p['verdict'] == 'accepted' else 'WEAK'} verdict={p['verdict']} "
                         f"stryker score={score:.2f} caught={mut.get('caught')}/{mut.get('total')} "
                         f"classification={ev.get('classification')}")
            notes.append(f"    test_path={p['test_path']} spec_check: {spec.get('reason')}")
            ok = ok and mut.get("engine") == "stryker" and p["verdict"] == "accepted" and score >= 0.5
            ok = ok and p["test_path"].endswith(".test.js")
        dirty = _git(ws, "status", "--porcelain")
        notes.append(f"{'ok ' if not dirty else 'WROTE FILES'} repository untouched"
                     + (f" — {dirty}" if dirty else ""))
        ok = ok and not dirty
    shutil.rmtree(ws, ignore_errors=True)
    return ok, notes, agent


def main(argv) -> int:
    trials = int(argv[1]) if len(argv) > 1 else 1
    host, port = "localhost", int(os.environ.get("NEURO_SAN_PORT", "8080"))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ok, notes, _ = _trial("tested", host, port)
    print(f"--- zero-candidate check: {'PASS' if ok else 'FAIL'}")
    for n in notes:
        print(f"    {n}")
    passes = 0
    for i in range(trials):
        t_ok, notes, agent = _trial("untested", host, port)
        print(f"--- trial {i + 1}/{trials}: {'PASS' if t_ok else 'FAIL'}")
        for n in notes:
            print(f"    {n}")
        if agent:
            print(f"    agent: {json.dumps(agent)[:200]}")
        passes += 1 if t_ok else 0
    print(f"\nP6-JS RESULT: zero-candidate {'PASS' if ok else 'FAIL'}; generation {passes}/{trials} pass")
    return 0 if ok and passes == trials else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
