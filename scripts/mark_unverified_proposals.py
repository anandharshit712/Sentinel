"""Relabel test proposals 9–14 as `characterization` (12 §8, PROGRESS "Tier 2 verdicts before 2026-09-26").

    PYTHONPATH=. python scripts/mark_unverified_proposals.py [--dry-run]

Before 2026-09-26 the blind-oracle verdict on a proposal was whatever the agent SAID it was: the
oracle tool ran 19 times and never once performed a comparison. Proposals 9–14 therefore carry an
intent verdict (`confirms_intent` / `disputed`) that nothing measured, and a classification built on
it. This merges `classification: characterization` plus the reason into each row's `evaluation`, so
the dashboard shows them as unverified. The previous label is kept under `reclassified_from`.

Idempotent: a row that already carries this reason is left alone. Missing rows are reported, not
created. Take a `pg_dump` of the schema first.
"""
import json
import sys

from dotenv import load_dotenv

load_dotenv(".env")

from db import dao, models  # noqa: E402

IDS = range(9, 15)
REASON = ("unverified: the intent verdict on this proposal was asserted by the blind-oracle agent, "
          "not measured — before 2026-09-26 the oracle tool never ran a comparison. Only the "
          "mutation and differential tiers were measured; nothing independent says what the code "
          "should do.")


def _label(ev: dict) -> str:
    return json.dumps({"classification": ev.get("classification"),
                       "intent_verdict": ev.get("intent_verdict"),
                       "classification_reason": (ev.get("classification_reason") or "")[:90]},
                      ensure_ascii=False)


def main() -> int:
    dry = "--dry-run" in sys.argv
    t = models.test_proposals
    with dao.get_engine().begin() as c:
        rows = {r["id"]: r for r in c.execute(t.select().where(t.c.id.in_(list(IDS)))).mappings()}
        for pid in IDS:
            row = rows.get(pid)
            if row is None:
                print(f"#{pid}: not found — skipped")
                continue
            ev = dict(row["evaluation"] or {})
            if ev.get("classification_reason") == REASON:
                print(f"#{pid}: already marked — unchanged  {_label(ev)}")
                continue
            print(f"#{pid} before: {_label(ev)}")
            ev["reclassified_from"] = {"classification": ev.get("classification"),
                                       "classification_reason": ev.get("classification_reason")}
            ev["classification"] = "characterization"
            ev["classification_reason"] = REASON
            print(f"#{pid} after:  {_label(ev)}")
            if not dry:
                c.execute(t.update().where(t.c.id == pid).values(evaluation=ev))
    print("dry run — nothing written" if dry else "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
