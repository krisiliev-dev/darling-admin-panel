# -*- coding: utf-8 -*-
"""Model-level data-integrity constraints, exercised both directly at the DB layer and through
the app. These are the backstops that keep the panel's own admin.db honest even if application
logic has a bug or two writers race:

  * users.uq_single_owner  — partial unique index: AT MOST ONE role='owner' ever (setup race)
  * cod_settlement.order_id — UNIQUE: the double-settle backstop (exactly one payout row per order)
  * pricing_group_rule (scope,key) — UNIQUE: setting a supplier rule twice UPDATES, never duplicates
  * order_outcome           — deliberately NON-unique: append-only refusal history (many rows/phone)

Plus a test-isolation sanity check that the whole suite is bound to a throwaway temp DB.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import async_session_maker, engine
from app.models import CodSettlement, OrderOutcome, PricingGroupRule, User
from app.routers import cod


# --------------------------------------------------------------------------------------------
# Test-DB isolation sanity — never the real admin.db
# --------------------------------------------------------------------------------------------
def test_suite_is_bound_to_throwaway_temp_db():
    url = str(engine.url)
    assert "admin_panel_tests_" in url, f"engine is not the temp DB: {url!r}"
    # the real DB is <backend>/admin.db — the temp path must not be it
    assert not url.rstrip("/").endswith("/admin.db"), f"engine points at the real admin.db: {url!r}"


# --------------------------------------------------------------------------------------------
# users.uq_single_owner — at most one owner (the first-run setup race, at the DB level)
# --------------------------------------------------------------------------------------------
async def test_second_owner_row_raises_integrityerror():
    # the app's /setup guard checks a user count first; this proves the DB itself is the final
    # backstop for two concurrent /setup calls that both pass that check and then both INSERT.
    async with async_session_maker() as s:
        s.add(User(email="owner1@example.com", hashed_password="h", role="owner"))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with async_session_maker() as s:
            s.add(User(email="owner2@example.com", hashed_password="h", role="owner"))
            await s.commit()


async def test_partial_index_only_covers_owners_so_many_staff_are_fine():
    # the unique index is partial (WHERE role='owner'); non-owner roles are unconstrained.
    async with async_session_maker() as s:
        s.add_all([
            User(email="a@example.com", hashed_password="h", role="staff"),
            User(email="b@example.com", hashed_password="h", role="staff"),
            User(email="c@example.com", hashed_password="h", role="packer"),
            User(email="d@example.com", hashed_password="h", role="manager"),
        ])
        await s.commit()
    async with async_session_maker() as s:
        n = (await s.execute(select(func.count()).select_from(User))).scalar_one()
    assert n == 4


async def test_second_setup_via_app_is_refused(owner_client):
    # end-to-end view of the same invariant: with an owner already set up, /setup refuses a second.
    r = await owner_client.post("/api/auth/setup", json={
        "email": "intruder@evil.com", "password": "another-pw-123456"})
    assert r.status_code == 409
    async with async_session_maker() as s:
        owners = (await s.execute(
            select(func.count()).select_from(User).where(User.role == "owner"))).scalar_one()
    assert owners == 1


# --------------------------------------------------------------------------------------------
# cod_settlement.order_id UNIQUE — the double-settle backstop
# --------------------------------------------------------------------------------------------
async def test_cod_settlement_order_id_is_unique():
    async with async_session_maker() as s:
        s.add(CodSettlement(order_id="o1", collected=30.0, expected=30.0))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with async_session_maker() as s:
            s.add(CodSettlement(order_id="o1", collected=99.0, expected=30.0))
            await s.commit()


@pytest.fixture
def settle_state(monkeypatch):
    """One delivered COD order awaiting settlement, wired into the cod router's order source."""
    state = {"o1": {"order_name": "#1", "status": "delivered", "courier": "econt",
                    "tracking": "111", "cod_amount": 30.0}}

    def _set(oid, name, **k):
        if "status" in k and oid in state:
            state[oid]["status"] = k["status"]

    monkeypatch.setattr(cod.shopify_orders, "state_map", lambda: state)
    monkeypatch.setattr(cod.shopify_orders, "set_state", _set)
    return state


async def test_double_settle_leaves_exactly_one_row(owner_client, settle_state):
    body = {"reference": "P1", "payout_date": "", "courier": "",
            "lines": [{"order_id": "o1", "collected": 30.0}]}
    r1 = await owner_client.post("/api/cod/settle", json=body)
    assert r1.status_code == 200 and r1.json()["settled"] == 1

    # second settle of the same order is skipped, not double-recorded (backed by the UNIQUE index)
    r2 = await owner_client.post("/api/cod/settle", json=body)
    j = r2.json()
    assert (j["ok"], j["settled"], j["discrepancies"], j["skipped"]) == (True, 0, 0, 1)

    async with async_session_maker() as s:
        n = (await s.execute(select(func.count()).select_from(CodSettlement)
                             .where(CodSettlement.order_id == "o1"))).scalar_one()
    assert n == 1


# --------------------------------------------------------------------------------------------
# pricing_group_rule UNIQUE(scope, key) — re-setting a supplier rule updates, never duplicates
# --------------------------------------------------------------------------------------------
async def test_pricing_group_rule_scope_key_is_unique_at_db_level():
    async with async_session_maker() as s:
        s.add(PricingGroupRule(scope="supplier", key="Мира-Н", markup_pct=0.5))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with async_session_maker() as s:
            s.add(PricingGroupRule(scope="supplier", key="Мира-Н", markup_pct=0.6))
            await s.commit()


async def test_same_scope_key_with_different_scope_is_allowed():
    # the constraint is on the PAIR: same key under a different scope is a distinct rule.
    async with async_session_maker() as s:
        s.add_all([
            PricingGroupRule(scope="supplier", key="Вертекс", markup_pct=0.5),
            PricingGroupRule(scope="category", key="Вертекс", markup_pct=0.2),
        ])
        await s.commit()
    async with async_session_maker() as s:
        n = (await s.execute(select(func.count()).select_from(PricingGroupRule))).scalar_one()
    assert n == 2


async def test_setting_supplier_rule_twice_updates_not_duplicates(owner_client):
    # via the app: POST /group for the same supplier twice → one row, latest markup wins.
    await owner_client.post("/api/pricing/group",
                            json={"scope": "supplier", "key": "Егера", "markup_pct": 0.4})
    r = await owner_client.post("/api/pricing/group",
                                json={"scope": "supplier", "key": "Егера", "markup_pct": 0.55})
    assert r.status_code == 200 and r.json()["markup_pct"] == 0.55
    async with async_session_maker() as s:
        rows = (await s.execute(select(PricingGroupRule).where(
            PricingGroupRule.scope == "supplier", PricingGroupRule.key == "Егера"))).scalars().all()
    assert len(rows) == 1 and rows[0].markup_pct == 0.55


# --------------------------------------------------------------------------------------------
# order_outcome — deliberately NOT unique: append-only refusal history
# --------------------------------------------------------------------------------------------
async def test_order_outcome_is_append_only_many_rows_per_phone():
    # unlike cod_settlement, OrderOutcome keeps every attempt; two refusals for one phone = 2 rows.
    async with async_session_maker() as s:
        s.add_all([
            OrderOutcome(phone_norm="359888123456", outcome="refused", order_id="o1"),
            OrderOutcome(phone_norm="359888123456", outcome="refused", order_id="o2"),
            OrderOutcome(phone_norm="359888123456", outcome="delivered", order_id="o3"),
        ])
        await s.commit()
    async with async_session_maker() as s:
        n = (await s.execute(select(func.count()).select_from(OrderOutcome)
                             .where(OrderOutcome.phone_norm == "359888123456"))).scalar_one()
    assert n == 3  # history preserved, not deduped


async def test_recording_outcome_twice_via_app_keeps_both(owner_client):
    # the risk router APPENDs; the internal-refusal count is what rises with repeat refusals.
    for i in range(2):
        r = await owner_client.post("/api/risk/outcome", json={
            "phone": "0888123456", "outcome": "refused", "order_name": f"#{i}", "cod_amount": 20})
        assert r.status_code == 201
    async with async_session_maker() as s:
        n = (await s.execute(select(func.count()).select_from(OrderOutcome)
                             .where(OrderOutcome.phone_norm == "359888123456"))).scalar_one()
    assert n == 2
