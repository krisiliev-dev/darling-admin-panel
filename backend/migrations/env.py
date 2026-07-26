# -*- coding: utf-8 -*-
"""Alembic migration environment for admin.db.

Adopted (2026-07-26) so the FIRST non-additive schema change has a real migration path — until now
the app auto-creates tables (create_all) + adds new columns on startup (db._backfill_columns), which
can't rename/drop/retype. The runtime startup is UNCHANGED; use Alembic explicitly for real changes:

    cd backend
    alembic revision --autogenerate -m "describe the change"   # generate
    alembic upgrade head                                        # apply

The bundled 0001 baseline captures the current schema. An EXISTING admin.db (already created by
create_all) adopts Alembic once with:  alembic stamp head
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# make `app` importable when alembic runs from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402,F401  (register all tables on Base.metadata)
from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    # migrations run on a SYNC driver; the app uses sqlite+aiosqlite at runtime
    return get_settings().admin_db_url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # render_as_batch=True → SQLite gets copy-and-move table rebuilds for ALTER (drop/rename/retype)
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
