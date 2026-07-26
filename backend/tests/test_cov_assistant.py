# -*- coding: utf-8 -*-
"""Асистент (/api/assistant) — grounded, read-only Q&A + backend status.

The answer is produced by assistant_llm.answer (subprocess/HTTP), which we monkeypatch so no
real model is called. The live snapshot reads pipeline sqlite/analytics through db.connect via
_local_snapshot; those are defensively wrapped (missing -> defaults), so the snapshot survives
against the throwaway test DB. We assert: happy path + grounding snapshot present, the
deterministic suggestion routing, 400 (empty question), 503 (all backends down), and auth.
"""

import run_one

import app.routers.assistant as assistant


def _stub_llm(monkeypatch, answer_text="Имате 2 чернови за създаване."):
    monkeypatch.setattr(assistant.assistant_llm, "answer",
                        lambda system, prompt: (answer_text, "qwen-flash"))
    # identity ru-fix so the answer passes through unchanged
    monkeypatch.setattr(run_one, "apply_ru_fix", lambda s: (s, False))


async def test_ask_happy_path_grounded(staff_client, monkeypatch, make_draft):
    await make_draft(code="A", name="Едно", status="approved")
    _stub_llm(monkeypatch, "Отговор.")
    r = await staff_client.post("/api/assistant/ask", json={"question": "Колко чернови имам?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Отговор."
    assert body["model"] == "qwen-flash"
    # the snapshot that grounded the answer is returned, and reflects the approved draft
    assert body["snapshot"]["чернови_за_създаване"] == 1
    assert "магазин_пуснат" in body["snapshot"]


async def test_ask_suggestions_from_intent(staff_client, monkeypatch):
    _stub_llm(monkeypatch)
    # a question about tracking -> the sync_tracking suggestion; capped at 2
    r = await staff_client.post("/api/assistant/ask",
                                json={"question": "Къде е пратката, проследяване?"})
    assert r.status_code == 200
    sugg = r.json()["suggestions"]
    assert any(s["kind"] == "sync_tracking" for s in sugg)
    assert len(sugg) <= 2


async def test_ask_inventory_intent_routes_to_inventory(staff_client, monkeypatch):
    _stub_llm(monkeypatch)
    r = await staff_client.post("/api/assistant/ask",
                                json={"question": "Каква е наличността, свърши ли нещо?"})
    sugg = r.json()["suggestions"]
    assert any(s.get("to") == "/inventory" for s in sugg)


async def test_ask_empty_question_is_400(staff_client, monkeypatch):
    _stub_llm(monkeypatch)
    r = await staff_client.post("/api/assistant/ask", json={"question": "   "})
    assert r.status_code == 400


async def test_ask_backend_down_is_503(staff_client, monkeypatch):
    def boom(system, prompt):
        raise RuntimeError("no model reachable")
    monkeypatch.setattr(assistant.assistant_llm, "answer", boom)
    r = await staff_client.post("/api/assistant/ask", json={"question": "Здрасти?"})
    assert r.status_code == 503
    assert "Асистентът е недостъпен" in r.json()["detail"]


async def test_ask_requires_auth(client):
    r = await client.post("/api/assistant/ask", json={"question": "Здрасти?"})
    assert r.status_code == 401


async def test_backends_lists_config(staff_client, monkeypatch):
    monkeypatch.setattr(assistant.assistant_llm, "configured_backends",
                        lambda: [{"key": "free", "ready": True}])
    r = await staff_client.get("/api/assistant/backends")
    assert r.status_code == 200, r.text
    assert r.json() == {"backends": [{"key": "free", "ready": True}]}


async def test_backends_requires_auth(client):
    r = await client.get("/api/assistant/backends")
    assert r.status_code == 401


# --- _anthropic backend now uses httpx (transport tests; it was never covered) ---
def test_anthropic_backend_parses_text(monkeypatch):
    import httpx

    from app import assistant_llm as al
    monkeypatch.setattr(al, "_cfg", lambda k, d="": "sk-test" if "KEY" in k else d)

    class _R:
        status_code = 200
        text = ""
        def json(self):
            return {"content": [{"type": "text", "text": "Здравей"}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    text, label = al._anthropic("sys", "hi")
    assert text == "Здравей" and "anthropic" in label


def test_anthropic_backend_raises_on_http_error(monkeypatch):
    import httpx
    import pytest as _pt

    from app import assistant_llm as al
    monkeypatch.setattr(al, "_cfg", lambda k, d="": "sk-test" if "KEY" in k else d)

    class _R:
        status_code = 429
        text = "rate limited"
        def json(self):
            return {}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    with _pt.raises(RuntimeError):
        al._anthropic("sys", "hi")
