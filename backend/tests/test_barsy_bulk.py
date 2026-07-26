# -*- coding: utf-8 -*-
"""G4 bulk-write resilience: batch baseline fetch + the barsy_api 501-retry gate.

Pacing (`_bulk.pace`) is exercised indirectly by the catalog/pricing bulk tests (it must not change
their outcome); here we cover the two mechanisms with observable behaviour."""
import io
import time
import urllib.error

import pytest

from app.core import barsy
from app.services import barsy_bulk as _bulk


# ---- price_baselines: one Articles_getlist page per chunk, parsed to {id: price|None} ----------
def test_price_baselines_batches_and_parses(monkeypatch):
    seen = []

    def fake_call(op, body=None, *a, **k):
        seen.append((op, body))
        assert op == "Articles_getlist"
        ids = body["filters"]["search_article_ids"]
        # return a row per id EXCEPT one that is missing (→ None) and one with a junk price (→ None)
        rows = []
        for aid in ids:
            if aid == 3:
                continue                      # no row → baseline None
            price = "n/a" if aid == 4 else str(aid * 10)
            rows.append({"article_id": aid, "actual_price": price})
        return rows

    monkeypatch.setattr(barsy, "call", fake_call)
    out = _bulk.price_baselines([1, 2, 3, 4])
    assert out == {1: 10.0, 2: 20.0, 4: None}   # 3 absent, 4 unparseable→None
    assert seen[0][0] == "Articles_getlist"
    assert seen[0][1]["filters"]["search_article_ids"] == [1, 2, 3, 4]


def test_price_baselines_chunks(monkeypatch):
    pages = []

    def fake_call(op, body=None, *a, **k):
        batch = body["filters"]["search_article_ids"]
        pages.append(len(batch))
        return [{"article_id": i, "actual_price": "1.00"} for i in batch]

    monkeypatch.setattr(barsy, "call", fake_call)
    out = _bulk.price_baselines(list(range(5)), chunk=2)
    assert pages == [2, 2, 1]                    # 5 ids over chunk=2 → 3 pages
    assert len(out) == 5


def test_price_baselines_failed_page_is_safe(monkeypatch):
    def boom(op, body=None, *a, **k):
        raise RuntimeError("barsy down")

    monkeypatch.setattr(barsy, "call", boom)
    assert _bulk.price_baselines([1, 2]) == {}   # never raises → caller falls back to cache


# ---- barsy_api.call: 501 (write-burst throttle) is retried, not raised on first hit -------------
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_501():
    return urllib.error.HTTPError("http://x", 501, "busy", {}, io.BytesIO(b"not implemented"))


def test_barsy_call_retries_501(monkeypatch):
    monkeypatch.setenv("BARSY_HOST", "example.test")
    monkeypatch.setenv("BARSY_USER", "u")
    monkeypatch.setenv("BARSY_PASS", "p")
    monkeypatch.setattr(time, "sleep", lambda *_: None)  # no real backoff wait (call() imports time)

    attempts = {"n": 0}

    def flaky(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _http_501()
        return _Resp('{"article_id": 42}')

    monkeypatch.setattr(barsy.urllib.request, "urlopen", flaky)
    out = barsy.call("Articles_update", {"article_id": 42})
    assert out == {"article_id": 42}
    assert attempts["n"] == 3                     # two 501s survived, third succeeded


def test_barsy_call_raises_501_after_exhausting_retries(monkeypatch):
    monkeypatch.setenv("BARSY_HOST", "example.test")
    monkeypatch.setenv("BARSY_USER", "u")
    monkeypatch.setenv("BARSY_PASS", "p")
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    monkeypatch.setattr(barsy.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_501()))
    with pytest.raises(barsy.BarsyError):
        barsy.call("Articles_update", {"article_id": 1})
