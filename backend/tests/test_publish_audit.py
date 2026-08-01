# -*- coding: utf-8 -*-
"""Publish-readiness audit (#81) — flags sellable Shopify variants not launch-ready (SKU=Barsy code,
tracked, ACTIVE; CONTINUE-policy is a note). The connector owns the Shopify enumeration; we mock it and
seed product_cache with the Barsy codes to cross-check."""
import app.routers.publish_audit as pa_module
from app.db import async_session_maker
from app.models import ProductCache


async def _seed_codes(*codes):
    async with async_session_maker() as s:
        for i, c in enumerate(codes):
            s.add(ProductCache(article_id=1000 + i, code=c, name=f"art {c}"))
        await s.commit()


async def test_flags_each_issue(owner_client, monkeypatch):
    await _seed_codes("0185186", "А716")
    variants = [
        # ready: SKU is a Barsy code, tracked, ACTIVE, DENY
        {"variant_id": "1", "sku": "0185186", "product_title": "Кош", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 9},
        # no SKU
        {"variant_id": "2", "sku": "", "product_title": "Табуретка", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 0},
        # SKU not in Barsy
        {"variant_id": "3", "sku": "ZZZ999", "product_title": "Х", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 0},
        # untracked + draft
        {"variant_id": "4", "sku": "А716", "product_title": "Y", "product_status": "DRAFT",
         "tracked": False, "policy": "DENY", "on_hand": 0},
        # backorderable note only (CONTINUE) — still "ready" (not blocked)
        {"variant_id": "5", "sku": "0185186", "product_title": "Z", "product_status": "ACTIVE",
         "tracked": True, "policy": "CONTINUE", "on_hand": 0},
    ]
    monkeypatch.setattr(pa_module.connector, "list_sellable_variants", lambda: variants, raising=False)
    monkeypatch.setattr(pa_module.reg, "zero_price_codes", lambda: set())
    monkeypatch.setattr(pa_module.reg, "image_codes", lambda: None)  # photo logic owned by test_no_photo_blocks_website_only

    r = await owner_client.get("/api/publish-audit")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["total"] == 5
    assert b["ready"] == 2                     # variant 1 (clean) + variant 5 (only a CONTINUE note)
    assert b["not_ready"] == 3                 # 2 (no_sku), 3 (sku_not_in_barsy), 4 (not_tracked+not_active)
    assert b["by_issue"]["no_sku"] == 1
    assert b["by_issue"]["sku_not_in_barsy"] == 1
    assert b["by_issue"]["not_tracked"] == 1
    assert b["by_issue"]["not_active"] == 1
    assert b["by_issue"]["policy_continue"] == 1
    # only variants WITH issues are returned in the detail list (5 has the CONTINUE note → included)
    ids = {v["variant_id"] for v in b["variants"]}
    assert ids == {"2", "3", "4", "5"}
    v4 = next(v for v in b["variants"] if v["variant_id"] == "4")
    assert set(v4["issues"]) == {"not_tracked", "not_active"} and v4["blocked"] is True
    v5 = next(v for v in b["variants"] if v["variant_id"] == "5")
    assert v5["issues"] == ["policy_continue"] and v5["blocked"] is False


async def test_missing_connector_fn_is_501(owner_client, monkeypatch):
    monkeypatch.delattr(pa_module.connector, "list_sellable_variants", raising=False)
    r = await owner_client.get("/api/publish-audit")
    assert r.status_code == 501


async def test_requires_auth(client):
    assert (await client.get("/api/publish-audit")).status_code == 401


async def test_no_photo_blocks_website_only(owner_client, monkeypatch):
    """Owner rule (2026-08-02): imageless products stay sellable at the till but are
    BLOCKED for the online store. Index absent (None) invents no issues — cannot-check
    and no-photos-exist must never look alike."""
    await _seed_codes("0190001", "0190002")
    variants = [
        {"variant_id": "1", "sku": "0190001", "product_title": "А", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1, "price": "3,10"},
        {"variant_id": "2", "sku": "0190002", "product_title": "Б", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1, "price": "3,10"},
    ]
    monkeypatch.setattr(pa_module.connector, "list_sellable_variants", lambda: variants,
                        raising=False)
    monkeypatch.setattr(pa_module.reg, "zero_price_codes", lambda: set())
    monkeypatch.setattr(pa_module.reg, "image_codes", lambda: {"190001"})   # int-key form
    b = (await owner_client.get("/api/publish-audit")).json()
    assert b["by_issue"]["no_photo"] == 1 and b["ready"] == 1
    v2 = next(v for v in b["variants"] if v["variant_id"] == "2")
    assert v2["issues"] == ["no_photo"] and v2["blocked"] is True

    # index absent → cannot check → NO no_photo issues
    monkeypatch.setattr(pa_module.reg, "image_codes", lambda: None)
    b2 = (await owner_client.get("/api/publish-audit")).json()
    assert "no_photo" not in b2["by_issue"]


async def test_zero_price_blocks_publishing(owner_client, monkeypatch):
    """Owner invariant (2026-08-02): price 0 never goes live. An explicit variant price of 0
    blocks; an unreadable one blocks as price_unknown (fail closed on money); a variant with
    NO price field falls back to the snapshot's Microinvest zero-price list."""
    await _seed_codes("0185186", "0177001")
    variants = [
        {"variant_id": "1", "sku": "0185186", "product_title": "А", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1, "price": 0},
        {"variant_id": "2", "sku": "0185186", "product_title": "Б", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1, "price": "n/a"},
        {"variant_id": "3", "sku": "0177001", "product_title": "В", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1},   # no price field → snapshot answers
        {"variant_id": "4", "sku": "0185186", "product_title": "Г", "product_status": "ACTIVE",
         "tracked": True, "policy": "DENY", "on_hand": 1, "price": "4,90"},
    ]
    monkeypatch.setattr(pa_module.connector, "list_sellable_variants", lambda: variants,
                        raising=False)
    monkeypatch.setattr(pa_module.reg, "zero_price_codes", lambda: {"0177001"})
    monkeypatch.setattr(pa_module.reg, "image_codes", lambda: {"185186", "177001"})

    b = (await owner_client.get("/api/publish-audit")).json()
    assert b["by_issue"]["zero_price"] == 2          # explicit 0 + snapshot-flagged
    assert b["by_issue"]["price_unknown"] == 1
    assert b["ready"] == 1                           # only the 4,90 variant
    flags = {v["variant_id"]: v["blocked"] for v in b["variants"]}
    assert flags == {"1": True, "2": True, "3": True}
