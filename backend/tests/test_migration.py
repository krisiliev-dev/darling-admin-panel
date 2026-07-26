# -*- coding: utf-8 -*-
"""The poor-man's column-backfill migration (app/db.py `_backfill_columns`).

SQLAlchemy's create_all makes missing TABLES but NEVER alters existing ones, so a new model
column silently breaks a persisted admin.db: create_all passes, then any SELECT that names the
new column fails with 'no such column'. `_backfill_columns` closes that gap by ALTER-ing in the
missing columns after create_all. These tests drive it directly against a THROWAWAY sqlite file
(never admin.db, never the suite's temp DB) holding an OLD version of a table, and assert:
  * a missing column (e.g. cod_settlement.waybill) is added, and a SELECT of every model column then works
  * the added column carries its constant DEFAULT (existing rows get it)
  * it is IDEMPOTENT (running twice is a clean no-op)
  * it does nothing when the table is already current
  * it skips tables that don't exist yet (create_all's job, not the backfill's)
"""
from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect

from app.db import _backfill_columns, _lit_default
from app.models import CodSettlement, PricingGroupRule

# columns the live model has that an OLD cod_settlement table would be missing
_MODEL_COLS = {c.name for c in CodSettlement.__table__.columns}


def _cols(conn, table):
    rows = conn.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()
    return {r[1] for r in rows}


def _make_engine(tmp_path, name="old_admin.db"):
    """A throwaway sync sqlite engine on its own temp file (not admin.db, not the async test DB)."""
    db = tmp_path / name
    return create_engine("sqlite:///" + db.as_posix(), future=True)


def _create_old_cod_settlement(conn):
    """An OLD schema: the primary key + order_id only. Everything else (waybill, expected,
    collected, courier, payout_ref, ...) is a column the current model added later."""
    conn.exec_driver_sql(
        'CREATE TABLE cod_settlement ('
        'id INTEGER PRIMARY KEY, '
        'order_id VARCHAR(64))')


def test_backfill_adds_missing_column_and_select_of_all_model_columns_works(tmp_path):
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            _create_old_cod_settlement(conn)
            assert "waybill" not in _cols(conn, "cod_settlement")  # precondition: OLD schema

            _backfill_columns(conn)

            cols = _cols(conn, "cod_settlement")
            assert "waybill" in cols                 # the flagship missing column is now present
            assert _MODEL_COLS <= cols               # every model column exists

            # a SELECT naming every model column must succeed (this is exactly what 500s on an
            # un-migrated live DB). Round-trip an insert too, so the columns are truly usable.
            collist = ", ".join(f'"{n}"' for n in _MODEL_COLS)
            conn.exec_driver_sql(
                'INSERT INTO cod_settlement (order_id, waybill) VALUES (?, ?)', ("o-new", "WB1"))
            got = conn.exec_driver_sql(
                f"SELECT {collist} FROM cod_settlement").fetchall()
            assert len(got) == 1
    finally:
        eng.dispose()


def test_backfill_applies_constant_default_to_existing_rows(tmp_path):
    # a row that predates the new columns must inherit their DEFAULT after the ALTER, not NULL.
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            _create_old_cod_settlement(conn)
            conn.exec_driver_sql(
                'INSERT INTO cod_settlement (id, order_id) VALUES (1, ?)', ("o1",))

            _backfill_columns(conn)

            # waybill default is "" and expected/collected default 0.0 (see _lit_default)
            waybill, expected, collected = conn.exec_driver_sql(
                'SELECT waybill, expected, collected FROM cod_settlement WHERE id = 1').fetchone()
            assert waybill == ""            # string default '' applied to the pre-existing row
            assert expected == 0.0 and collected == 0.0
    finally:
        eng.dispose()


def test_backfill_is_idempotent(tmp_path):
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            _create_old_cod_settlement(conn)
            _backfill_columns(conn)
            after_first = _cols(conn, "cod_settlement")
            # running it a SECOND time must not raise and must not change the schema
            _backfill_columns(conn)
            after_second = _cols(conn, "cod_settlement")
        assert after_first == after_second == _MODEL_COLS  # stable, no dupes, no error
    finally:
        eng.dispose()


def test_backfill_is_noop_when_table_already_current(tmp_path):
    # a table created straight from the model metadata has no missing columns → nothing to add.
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            CodSettlement.__table__.create(conn)
            before = _cols(conn, "cod_settlement")
            _backfill_columns(conn)
            after = _cols(conn, "cod_settlement")
        assert before == after == _MODEL_COLS
    finally:
        eng.dispose()


def test_backfill_skips_tables_that_do_not_exist_yet(tmp_path):
    # empty DB, zero tables: the backfill must not raise and must not CREATE any table
    # (creating tables is create_all's job; the backfill only ALTERs existing ones).
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            _backfill_columns(conn)
            assert sa_inspect(conn).get_table_names() == []
    finally:
        eng.dispose()


def test_backfill_handles_a_second_model_table_independently(tmp_path):
    # prove the pass is per-table: an OLD pricing_group_rule (missing markup_pct) in the SAME db
    # as an absent cod_settlement gets its column added, and the absent table is left uncreated.
    eng = _make_engine(tmp_path)
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql(
                'CREATE TABLE pricing_group_rule ('
                'id INTEGER PRIMARY KEY, scope VARCHAR(16), key VARCHAR(255))')

            _backfill_columns(conn)

            pcols = _cols(conn, "pricing_group_rule")
            assert {c.name for c in PricingGroupRule.__table__.columns} <= pcols
            assert "markup_pct" in pcols
            # cod_settlement was never present and must NOT have been created by the backfill
            assert "cod_settlement" not in sa_inspect(conn).get_table_names()
    finally:
        eng.dispose()


def test_lit_default_only_emits_safe_constant_defaults():
    # unit-level guard on the helper the ALTER uses: constants → a literal, callables/None → None
    # (so a func.now()/uuid4 column is added nullable, never with a bogus DEFAULT).
    cols = {c.name: c for c in CodSettlement.__table__.columns}
    assert _lit_default(cols["waybill"]) == "''"        # str default '' → quoted literal
    assert _lit_default(cols["expected"]) == "0.0"      # float default 0.0 → numeric literal
    # settled_ts uses server_default=func.now() (no client-side default) → no literal
    assert _lit_default(cols["settled_ts"]) is None
