"""Was the gate right? Calibration report on the command line (10 §3 P4.3).

    PYTHONPATH=. python scripts/calibration.py            # the report
    PYTHONPATH=. python scripts/calibration.py --json     # machine-readable

Reads the decisions, outcomes, approvals and proposals already in the database. Recommendations
are arguments for a human to weigh, never changes — nothing in this script writes anything.
"""
import json
import sys

from db import dao
from lib import calibration


def _cell(c: dict) -> str:
    r = c.get("rate")
    if c.get("insufficient_data"):
        return f"—      (n={c['n']}, need {calibration.MIN_SAMPLE})"
    return f"{r:>5.0%}  (n={c['n']})"


def main(argv) -> int:
    report = calibration.report(
        dao.list_decisions_with_band(),
        dao.list_outcomes(),
        dao.list_all_approvals(),
        dao.list_test_proposals(limit=1000),
    )
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
        return 0

    t = report["totals"]
    print(f"\nSentinel calibration — {t['decisions']} decision(s), {t['promotions']} promoted, "
          f"{t['gated']} gated, {t['outcomes_recorded']} outcome(s) recorded\n")

    if not t["decisions"]:
        print("  No decisions recorded yet. Calibration needs history: run the pipeline, record")
        print("  what happened afterwards with POST /api/v1/runs/{id}/outcome, come back.\n")
        return 0

    repos = report.get("repos") or {}
    if repos:
        shown = ", ".join(f"{r} ({n})" for r, n in list(repos.items())[:6])
        print(f"  evidence from: {shown}")
        if all(any(k in r for k in ("demo", "test", "fixture", "p2-", "p3-", "b4-", "ui-"))
               for r in repos):
            print("  NOTE: every decision here came from demo or verification runs. Treat the")
            print("        recommendations as a check that the machinery works, not as advice")
            print("        about a real gate.")
        print()

    print("PROMOTIONS — how often did a promotion at this band go wrong?")
    if not report["promotion_by_band"]:
        print("  (none yet)")
    for band, c in report["promotion_by_band"].items():
        print(f"  {band:<10} {_cell(c['bad_rate'])}   bad={c['bad']} unknown={c['unknown']}")

    print("\nESCALATIONS — when the gate stopped a change, did the human agree?")
    if not report["escalation_by_band"]:
        print("  (none yet)")
    for band, c in report["escalation_by_band"].items():
        print(f"  {band:<10} approved {_cell(c['approval_rate'])}   "
              f"gated={c['gated']} unresolved={c['unresolved']}")

    if report["generated_tests"]:
        print("\nGENERATED TESTS — are the accepted ones being kept?")
        for verdict, c in report["generated_tests"].items():
            print(f"  {verdict:<12} adopted {_cell(c['adoption_rate'])}   "
                  f"proposed={c['proposed']} caught_real_bug={c['caught_bug']}")

    recs = report["recommendations"]
    print(f"\nRECOMMENDATIONS ({len(recs)}) — arguments, not changes. Nothing below has been applied.")
    if not recs:
        print("  None. Either the gate looks calibrated, or there is not enough evidence to say.")
    for r in recs:
        print(f"\n  [{r['confidence']} confidence] {r['kind']} at band {r['band']}")
        print(f"    saw:     {r['evidence']}")
        print(f"    suggest: {r['suggestion']}")
        print(f"    caveat:  {r['caveat']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
