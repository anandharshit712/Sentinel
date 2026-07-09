"""Seed demo data (07 §6.3): a few recent incidents so the env-context factor has signal.

    PYTHONPATH=. python scripts/seed_demo.py

Inserts incidents for the demo repos/envs used by scripts/verify_c.py (idempotent: clears the
demo repos' rows first). incident_history_tool then returns count_7d/count_30d > 0, and the
RiskScore env factor + EnvContext card light up. Safe to re-run.
"""
import datetime as _dt
import sys

from db import dao, models

# (repo, env, kind, days_ago) — repo names match scripts/verify_c.py events
_SEED = [
    ("c-insecure", "staging", "rollback", 2),
    ("c-insecure", "staging", "sev2", 12),
    ("c-happy", "test", "flaky_deploy", 20),
]
_REPOS = sorted({r for r, *_ in _SEED})


def main() -> int:
    now = _dt.datetime.now(_dt.timezone.utc)
    eng = dao.get_engine()
    t = models.incidents
    with eng.begin() as c:
        c.execute(t.delete().where(t.c.repo.in_(_REPOS)))  # idempotent reseed
        for repo, env, kind, days in _SEED:
            c.execute(t.insert().values(
                repo=repo, env=env, kind=kind,
                occurred_at=now - _dt.timedelta(days=days),
                detail={"seeded": True}))
    for repo, env in {(r, e) for r, e, *_ in _SEED}:
        print(f"{repo}/{env}: 7d={dao.recent_incidents(repo, env, 7)['count']} "
              f"30d={dao.recent_incidents(repo, env, 30)['count']}")
    print("seed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
