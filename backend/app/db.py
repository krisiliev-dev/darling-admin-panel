# -*- coding: utf-8 -*-
"""Async SQLAlchemy engine for admin.db — the panel's OWN database (users + audit).
Kept separate from agent-pipeline/pipeline.db (the catalog system-of-record, which the
live enrichment fleet writes). We only ever create/alter tables here."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

_settings = get_settings()
engine = create_async_engine(_settings.admin_db_url, echo=False, future=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _lit_default(col):
    """A SQL literal for a column's constant default, for ALTER TABLE ADD COLUMN. Returns None if
    there's no safe constant default (then the added column is nullable)."""
    d = getattr(col.default, "arg", None)
    if d is None or callable(d):
        return None
    if isinstance(d, bool):                 # bool is an int subclass — check first; SQLite wants 0/1
        return "1" if d else "0"
    return repr(d) if isinstance(d, str) else str(d)


def _backfill_columns(sync_conn):
    """Add columns that exist on the models but not yet in the DB. SQLAlchemy's create_all makes
    missing TABLES but never alters existing ones, so a new model column silently breaks queries
    against an existing DB (create_all passes, then SELECT fails: 'no such column'). This idempotent
    pass keeps a persisted admin.db in step with the models — a poor-man's migration (no Alembic)."""
    for table in Base.metadata.sorted_tables:
        rows = sync_conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')").fetchall()
        if not rows:
            continue  # table absent → create_all just made it fresh (already correct)
        existing = {r[1] for r in rows}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
            lit = _lit_default(col)
            if lit is not None:
                ddl += f" DEFAULT {lit}"
            sync_conn.exec_driver_sql(ddl)


async def create_db_and_tables() -> None:
    # import models so they register on Base.metadata before create_all
    from . import models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_backfill_columns)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
