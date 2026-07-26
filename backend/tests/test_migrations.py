# -*- coding: utf-8 -*-
"""Alembic migration chain integrity — a fresh `alembic upgrade head` must produce the SAME schema
the models declare (create_all). Guards against drift: if someone adds a model column but forgets the
migration, the two schemas diverge and this fails. (The runtime adopts an existing create_all DB via
`stamp head`; that adoption is only correct while the chain matches the models — which this asserts.)"""
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app import models  # noqa: F401  (register tables on Base.metadata)
from app.db import Base

_BACKEND = Path(__file__).resolve().parent.parent


def _schema(url: str) -> dict[str, set[str]]:
    eng = create_engine(url)
    try:
        insp = inspect(eng)
        return {t: {c["name"] for c in insp.get_columns(t)}
                for t in insp.get_table_names() if t != "alembic_version"}
    finally:
        eng.dispose()


def test_alembic_head_matches_models(tmp_path):
    # DB built by running the migration chain to head
    mig = tmp_path / "mig.db"
    env = {**os.environ, "ADMIN_DB_URL": f"sqlite+aiosqlite:///{mig.as_posix()}"}
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=_BACKEND, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # DB built directly from the models
    direct = tmp_path / "direct.db"
    eng = create_engine(f"sqlite:///{direct.as_posix()}")
    Base.metadata.create_all(eng)
    eng.dispose()

    assert _schema(f"sqlite:///{mig.as_posix()}") == _schema(f"sqlite:///{direct.as_posix()}")
