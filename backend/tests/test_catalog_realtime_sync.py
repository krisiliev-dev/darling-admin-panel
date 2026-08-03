# -*- coding: utf-8 -*-
"""Real-time catalogue mirror.

Barsy is the system of record; product_cache is a snapshot of it that used to drift silently (a
UPSERT-only sync that ran once left 38 articles deleted in Barsy on display in the panel for days).
These tests pin the two halves of the fix:
  * the Barsy `articles` webhook maintains the cache LIVE - create/update refresh a row, a delete
    REMOVES it - and never destroys anything on an unreadable payload or a failed ERP read;
  * POST /api/catalog/sync is a true reconciliation that prunes what Barsy no longer has, but
    REFUSES to prune on a short or failed fetch, and records the freshness the UI shows.
Barsy is monkeypatched throughout - the suite never touches the live ERP.
"""
import pytest

from app.routers import webhooks

_TOKEN = "test-barsy-token"       # conftest sets BARSY_WEBHOOK_TOKEN to this


def _art(aid, code, name="Тиган 24см", price="9.90"):
    return {"article_id": aid, "code": code, "article_name": name, "actual_price": price}


class _Barsy:
    """Routes Articles_getlist by request body: the full-sync call (filters={}) and the per-code
    read the webhook does are the same Barsy operation but must answer differently, and the whole
    point of the fix is what happens when the two disagree."""

    def __init__(self, recorder):
        self.full: list = []          # what the full sync sees
        self.by_code: dict = {}       # code -> article the per-code read sees; absent = gone from Barsy
        self.fail = False             # simulate an ERP that will not answer
        recorder.set_return("Store_amounts", {"rows": []})
        recorder.set_return("Articles_getlist", self._getlist)

    def _getlist(self, body):
        if self.fail:
            raise RuntimeError("Barsy unreachable")
        code = (body.get("filters") or {}).get("code")
        if code:
            a = self.by_code.get(str(code))
            return [a] if a else []
        return self.full if body.get("page_num", 1) == 1 else []


@pytest.fixture(autouse=True)
def _clear_module_state():
    """The replay guard and the unknown-event-shape memo are process-global, like the rate limiter."""
    webhooks._seen_deliveries.clear()
    webhooks._seen_event_shapes.clear()
    yield
    webhooks._seen_deliveries.clear()
    webhooks._seen_event_shapes.clear()


@pytest.fixture
def barsy(barsy_recorder, monkeypatch):
    """A scripted Barsy + a stubbed Shopify mirror (the Shopify half is covered in test_webhooks)."""
    monkeypatch.setattr(webhooks.connector, "barsy_article_to_shopify", lambda *a, **k: {"ok": True})
    return _Barsy(barsy_recorder)


async def _hook(client, payload):
    return await client.post("/webhooks/barsy/article", json=payload,
                             headers={"x-darling-token": _TOKEN})


async def _codes(client) -> set:
    r = (await client.get("/api/catalog/search?per_page=200")).json()
    return {i["code"] for i in r["items"]}


async def _status(client) -> dict:
    return (await client.get("/api/catalog/cache-status")).json()


async def _sync(client):
    return await client.post("/api/catalog/sync")


# --- webhook: create / update -------------------------------------------------------
async def test_webhook_create_adds_cache_row(owner_client, barsy):
    barsy.by_code["NEW-1"] = _art(101, "NEW-1", "Тиган 24см", "12.50")
    r = await _hook(owner_client, {"code": "NEW-1", "event": "create"})
    assert r.status_code == 200 and r.json()["event"] == "changed"
    assert await _codes(owner_client) == {"NEW-1"}


async def test_webhook_update_refreshes_cache_row(owner_client, barsy):
    barsy.full = [_art(1, "A-1", "Тиган 24см", "10.00")]
    assert (await _sync(owner_client)).status_code == 200
    # the article is renamed + repriced in Barsy; the webhook must carry that through immediately
    barsy.by_code["A-1"] = _art(1, "A-1", "Чиния бяла", "20.00")
    assert (await _hook(owner_client, {"article": {"code": "A-1"}, "event": "update"})).status_code == 200
    item = (await owner_client.get("/api/catalog/search?q=a-1")).json()["items"][0]
    assert item["name"] == "Чиния бяла" and item["price"] == 20.0


async def test_webhook_records_last_event_at(owner_client, barsy):
    """Staleness is watched, not errors: a subscription that quietly died must be visible."""
    assert (await _status(owner_client))["last_event_at"] is None
    barsy.by_code["W-1"] = _art(5, "W-1")
    await _hook(owner_client, {"code": "W-1"})
    st = await _status(owner_client)
    assert st["last_event_at"] and st["last_event_kind"] == "updated"


# --- webhook: delete ----------------------------------------------------------------
async def test_webhook_delete_removes_cache_row(owner_client, barsy):
    barsy.full = [_art(1, "A-1"), _art(2, "B-2")]
    await _sync(owner_client)
    assert await _codes(owner_client) == {"A-1", "B-2"}
    barsy.by_code = {"A-1": _art(1, "A-1")}          # B-2 has been deleted in Barsy
    r = await _hook(owner_client, {"code": "B-2", "action": "delete"})
    assert r.status_code == 200 and r.json()["event"] == "deleted"
    assert await _codes(owner_client) == {"A-1"}
    assert (await _status(owner_client))["last_event_kind"] == "deleted"


async def test_webhook_removes_row_without_any_delete_hint(owner_client, barsy):
    """Barsy's events are COARSE (one `articles` event, no verified per-action name), so removal must
    not depend on recognising a delete keyword: an article the ERP no longer returns is gone."""
    barsy.full = [_art(1, "A-1"), _art(2, "B-2")]
    await _sync(owner_client)
    barsy.by_code = {"A-1": _art(1, "A-1")}
    r = await _hook(owner_client, {"code": "B-2"})    # no event/action marker at all
    assert r.status_code == 200 and r.json()["event"] == "changed"
    assert await _codes(owner_client) == {"A-1"}


async def test_webhook_delete_is_idempotent(owner_client, barsy):
    barsy.full = [_art(1, "A-1"), _art(2, "B-2")]
    await _sync(owner_client)
    barsy.by_code = {"A-1": _art(1, "A-1")}
    for _ in range(2):                                # Barsy re-delivers; the second must be a no-op
        assert (await _hook(owner_client, {"code": "B-2", "action": "delete"})).status_code == 200
    assert await _codes(owner_client) == {"A-1"}


async def test_failed_barsy_read_never_deletes(owner_client, barsy):
    """Fail closed on removal: 'the ERP did not answer' must never collapse into 'it is gone'."""
    barsy.full = [_art(1, "A-1"), _art(2, "B-2")]
    await _sync(owner_client)
    barsy.fail = True
    r = await _hook(owner_client, {"code": "B-2", "action": "delete"})
    assert r.status_code == 200                       # still acked - a 500 only buys a retry storm
    assert await _codes(owner_client) == {"A-1", "B-2"}


# --- webhook: unknown / unparseable payloads ----------------------------------------
async def test_unknown_payload_is_logged_and_changes_nothing(owner_client, barsy, caplog):
    barsy.full = [_art(1, "A-1")]
    await _sync(owner_client)
    with caplog.at_level("INFO", logger="darling.webhooks"):
        r = await _hook(owner_client, {"entity": "articles", "type": "articles", "rec": {"x": 1}})
    assert r.status_code == 200 and r.json()["code"] == ""
    assert "unrecognised event markers" in caplog.text and "'articles'" in caplog.text
    assert await _codes(owner_client) == {"A-1"}      # no data touched


async def test_unparseable_body_is_200_and_changes_nothing(owner_client, barsy):
    barsy.full = [_art(1, "A-1")]
    await _sync(owner_client)
    r = await owner_client.post("/webhooks/barsy/article", content=b"<<not json>>",
                                headers={"x-darling-token": _TOKEN,
                                         "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["ignored"] is True
    assert await _codes(owner_client) == {"A-1"}


def test_barsy_event_detects_delete_hints():
    assert webhooks._barsy_event({"code": "X", "action": "delete"}) == "deleted"
    assert webhooks._barsy_event({"code": "X", "event": "deleted"}) == "deleted"
    assert webhooks._barsy_event({"article": {"code": "X", "delete_flag": 1}}) == "deleted"
    assert webhooks._barsy_event({"code": "X", "delete_flag": 0}) == "changed"   # falsy flag != delete
    assert webhooks._barsy_event({"code": "X", "event": "update"}) == "changed"
    assert webhooks._barsy_event({"code": "X"}) == "changed"
    assert webhooks._barsy_event(None) == "changed"


# --- sync: reconciliation + prune safety --------------------------------------------
async def test_sync_prunes_articles_deleted_in_barsy(owner_client, barsy):
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 11)]
    await _sync(owner_client)
    assert len(await _codes(owner_client)) == 10
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 10)]      # C-10 deleted in Barsy
    body = (await _sync(owner_client)).json()
    assert body["pruned"] == 1 and body["prune_refused"] is False
    assert "C-10" not in await _codes(owner_client)


async def test_sync_refuses_to_prune_on_empty_fetch(owner_client, barsy):
    """A fetch that returns nothing is a failure signal, not an empty catalogue."""
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 4)]
    await _sync(owner_client)
    before = await _status(owner_client)
    barsy.full = []
    body = (await _sync(owner_client)).json()
    assert body["prune_refused"] is True and body["pruned"] == 0
    assert len(await _codes(owner_client)) == 3                 # catalogue survived
    after = await _status(owner_client)
    assert after["last_status"] == "short_fetch"
    assert after["last_sync"] == before["last_sync"]            # an incomplete pass is not a sync


async def test_sync_refuses_to_prune_on_short_fetch(owner_client, barsy):
    """A throttled / short-paginated read returns fewer articles than exist; pruning on it would
    delete live products."""
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 11)]
    await _sync(owner_client)
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 8)]       # 7 of 10 = below the safe fraction
    body = (await _sync(owner_client)).json()
    assert body["prune_refused"] is True and body["pruned"] == 0
    assert len(await _codes(owner_client)) == 10
    assert body["note"]                                          # the refusal is surfaced, not silent


async def test_sync_records_last_sync_and_counts(owner_client, barsy):
    assert (await _status(owner_client))["last_sync"] is None    # never reconciled yet
    barsy.full = [_art(i, f"C-{i}") for i in range(1, 4)]
    await _sync(owner_client)
    st = await _status(owner_client)
    assert st["last_sync"] and st["last_status"] == "ok"
    assert st["cached_count"] == 3 and st["count"] == 3 and st["live_count"] == 3
    assert st["last_change"]


async def test_sync_failure_leaves_cache_intact_and_502s(owner_client, barsy):
    barsy.full = [_art(1, "A-1"), _art(2, "B-2")]
    await _sync(owner_client)
    barsy.fail = True
    assert (await _sync(owner_client)).status_code == 502
    assert await _codes(owner_client) == {"A-1", "B-2"}
    assert (await _status(owner_client))["last_status"] == "error"
