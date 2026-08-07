# -*- coding: utf-8 -*-
"""P1: receiving in packs. The supplier bills 6 каси, 72 glasses arrive — booking 6 is the drift
this slice removes. qty stays in PIECES; the pack is a conversion, never a second stock balance."""
import pytest

from app.db import async_session_maker
from app.models import DeliveryBatch, DeliveryLine
from app.services import packages as PK

SUP = "ТЕСТ-P1"
# mirrors the four rows live in Barsy on 2026-08-08 — note „Стек" twice, 6 and 24
FAKE_PACKS = [
    {"id": 1, "name": "Каса", "qty": 12.0},
    {"id": 6, "name": "Кашон", "qty": 8.0},
    {"id": 11, "name": "Стек", "qty": 6.0},
    {"id": 16, "name": "Стек", "qty": 24.0},
]


async def _batch_line(qty=0.0):
    async with async_session_maker() as s:
        b = DeliveryBatch(supplier=SUP, depot_id=1, reference="P1", doc_date="", source="file",
                          status="draft", created_by="t@t")
        s.add(b)
        await s.flush()
        ln = DeliveryLine(batch_id=b.id, supplier_code="P1", name="Чаша", qty=qty, price=1.0,
                          is_new=False, match_code="14153", article_id=999, status="pending")
        s.add(ln)
        await s.commit()
        return b.id, ln.id


# ── normalising what Barsy returns ───────────────────────────────────────────
def test_normalise_keeps_only_usable_packs():
    """A zero-quantity pack would silently turn a delivery into nothing."""
    rows = [
        {"package_id": 1, "name": "Каса", "amount": "12"},
        {"id": 6, "name": "Кашон", "quantity": 8},
        {"id": 99, "name": "Счупена", "amount": 0},      # unusable
        {"id": "x", "name": "Боклук", "amount": 5},      # non-numeric id
        "not a dict",
    ]
    out = PK._normalise(rows)
    assert [(p["id"], p["qty"]) for p in out] == [(1, 12.0), (6, 8.0)]


def test_by_id_never_matches_by_name(monkeypatch):
    """„Стек" is 6 AND 24. Matching by name would pick one arbitrarily and multiply by the wrong
    number — the exact error this slice exists to prevent."""
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    assert PK.by_id(11)["qty"] == 6.0
    assert PK.by_id(16)["qty"] == 24.0
    assert PK.by_id(999) is None


def test_fetch_survives_barsy_being_down(monkeypatch):
    """A delivery must stay usable without Barsy; the operator loses suggestions, not the page."""
    monkeypatch.setattr(PK, "_cache", None)

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(PK.barsy, "call", boom)
    assert PK.fetch(force=True) == []


# ── applying a pack ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_six_cases_of_twelve_becomes_seventy_two_pieces(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack",
                                json={"pack_count": 6, "pack_id": 1})
    assert r.status_code == 200, r.text
    line = r.json()["line"]
    assert line["qty"] == 72.0            # PIECES, canonical
    assert line["pack_count"] == 6.0 and line["pack_factor"] == 12.0 and line["pack_id"] == 1


@pytest.mark.asyncio
async def test_the_client_cannot_choose_the_factor_when_it_names_a_pack(owner_client, monkeypatch):
    """The factor is resolved server-side, so a stale page cannot multiply by a number Barsy no
    longer holds."""
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack",
                                json={"pack_count": 2, "pack_id": 11, "pack_factor": 1000})
    assert r.json()["line"]["qty"] == 12.0        # 2 × 6, not 2 × 1000


@pytest.mark.asyncio
async def test_an_unknown_pack_is_refused_not_guessed(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack",
                                json={"pack_count": 2, "pack_id": 4242})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_manual_factor_is_allowed_for_a_pack_barsy_does_not_know(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack",
                                json={"pack_count": 3, "pack_factor": 4})
    assert r.json()["line"]["qty"] == 12.0 and r.json()["line"]["pack_id"] is None


@pytest.mark.asyncio
async def test_nonsense_is_refused(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    for body in ({"pack_count": 0, "pack_id": 1},
                 {"pack_count": -2, "pack_id": 1},
                 {"pack_count": 2, "pack_factor": 0},
                 {"pack_count": 2}):                       # neither a pack nor a factor
        r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack", json=body)
        assert r.status_code == 400, body


@pytest.mark.asyncio
async def test_pack_refuses_a_finalising_delivery(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    bid, lid = await _batch_line()
    async with async_session_maker() as s:
        (await s.get(DeliveryBatch, bid)).status = "finalising"
        await s.commit()
    r = await owner_client.post(f"/api/deliveries/{bid}/lines/{lid}/pack",
                                json={"pack_count": 2, "pack_id": 1})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_packages_endpoint_lists_them(owner_client, monkeypatch):
    monkeypatch.setattr(PK, "fetch", lambda *a, **k: FAKE_PACKS)
    r = await owner_client.get("/api/deliveries/packages")
    assert r.status_code == 200
    assert {p["id"] for p in r.json()["packages"]} == {1, 6, 11, 16}
