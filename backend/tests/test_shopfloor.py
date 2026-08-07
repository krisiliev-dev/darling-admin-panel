# -*- coding: utf-8 -*-
"""Вратата на склада (kiosk): the panel-local shopfloor ledger.

The two design rules under test: the kiosk NEVER blocks (an unknown barcode is recorded raw,
not refused), and the ledger is panel-local (no Barsy call anywhere on this path). The known
EAN anchor is the same one the delivery suite trusts: 8696287052034 → our code 14153."""

import pytest

KNOWN_EAN = "8696287052034"
KNOWN_CODE = "14153"


@pytest.mark.asyncio
async def test_move_known_ean_resolves(owner_client):
    r = await owner_client.post("/api/shopfloor/move",
                                json={"barcode": KNOWN_EAN, "direction": "out"})
    assert r.status_code == 200, r.text
    m = r.json()["move"]
    assert m["known"] is True and m["code"] == KNOWN_CODE
    assert m["direction"] == "out" and m["qty"] == 1


@pytest.mark.asyncio
async def test_move_unknown_barcode_is_recorded_not_refused(owner_client):
    # NB "0000000000000" would be WRONG here: it exists in barcodes.jsonl (suppliers
    # ship placeholder codes), which is itself worth knowing about the kiosk resolver.
    r = await owner_client.post("/api/shopfloor/move",
                                json={"barcode": "NO-SUCH-BARCODE-1", "direction": "out", "qty": 2})
    assert r.status_code == 200, r.text                     # the kiosk never blocks
    m = r.json()["move"]
    assert m["known"] is False and m["code"] == "" and m["qty"] == 2
    # and it IS in the ledger, reviewable
    moves = (await owner_client.get("/api/shopfloor/moves")).json()["moves"]
    assert any(x["barcode"] == "NO-SUCH-BARCODE-1" for x in moves)


@pytest.mark.asyncio
async def test_move_validation(owner_client):
    for body in ({"barcode": "", "direction": "out"},
                 {"barcode": "123", "direction": "sideways"},
                 {"barcode": "123", "direction": "out", "qty": 0},
                 {"barcode": "123", "direction": "out", "qty": 501}):
        r = await owner_client.post("/api/shopfloor/move", json=body)
        assert r.status_code == 400, (body, r.text)


@pytest.mark.asyncio
async def test_move_batch_applies_counts(owner_client):
    r = await owner_client.post("/api/shopfloor/move-batch",
                                json={"counts": {KNOWN_EAN: 3, "1112223334445": 1},
                                      "direction": "out"})
    assert r.status_code == 200, r.text
    moves = r.json()["moves"]
    assert len(moves) == 2
    by_bc = {m["barcode"]: m for m in moves}
    assert by_bc[KNOWN_EAN]["qty"] == 3 and by_bc[KNOWN_EAN]["known"] is True
    assert by_bc["1112223334445"]["known"] is False


@pytest.mark.asyncio
async def test_onfloor_nets_out_minus_back(owner_client):
    await owner_client.post("/api/shopfloor/move-batch",
                            json={"counts": {KNOWN_EAN: 3}, "direction": "out"})
    await owner_client.post("/api/shopfloor/move",
                            json={"barcode": KNOWN_EAN, "direction": "back"})
    onfloor = (await owner_client.get("/api/shopfloor/onfloor")).json()["onfloor"]
    row = next(x for x in onfloor if x["key"] == KNOWN_CODE)
    assert row["net"] == 2                                  # 3 out − 1 back


@pytest.mark.asyncio
async def test_staff_can_use_the_kiosk(staff_client):
    # the tablet is logged in as ordinary staff — the door must not need a manager
    r = await staff_client.post("/api/shopfloor/move",
                                json={"barcode": KNOWN_EAN, "direction": "out"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_anonymous_is_refused(client):
    r = await client.post("/api/shopfloor/move",
                          json={"barcode": KNOWN_EAN, "direction": "out"})
    assert r.status_code == 401
