"""Alembic environment for Sentinel.

All objects live in the `sentinel` schema (never `public`): search_path is set on the
migration connection and alembic's own version table is pinned to `sentinel` too.
DATABASE_URL comes from .env (repo root).
"""
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()  # repo-root .env when run as: alembic -c db/alembic.ini ...

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.environ["DATABASE_URL"]
config.set_main_option("sqlalchemy.url", DATABASE_URL)

SCHEMA = "sentinel"
target_metadata = None  # DDL is hand-written (op.execute); no autogenerate


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # search_path=sentinel comes from the role default (ALTER ROLE ... SET search_path);
        # don't SET it here — doing so opens a transaction alembic won't commit.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
