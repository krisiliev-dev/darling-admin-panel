# -*- coding: utf-8 -*-
"""Processing-issues system — the panel must SURFACE connector refusals/anomalies, not swallow them
(owner 2026-07-27). Covers the capture (note_shopify_result), dedup, the /api/issues list+resolve, and
the nav badge count."""
from app.issues import note_shopify_result


async def test_records_unmatched_sku(owner_client):
    # the #1001 case: a line item whose SKU matches no Barsy article → connector refuses → we record it
    payload = {"name": "#1001", "id": 1001}
    result = {"missing_skus": ["Табуретка стълба"], "reason": "no article matched"}
    assert await note_shopify_result("orders/create", payload, result) is True

    body = (await owner_client.get("/api/issues")).json()
    assert body["open_count"] == 1
    iss = body["issues"][0]
    assert iss["kind"] == "unmatched_sku" and iss["order_ref"] == "#1001" and iss["severity"] == "error"
    assert "Табуретка стълба" in str(iss["detail"])


async def test_dedup_bumps_count_not_rows(owner_client):
    # a Shopify retry storm (create + updated + retries) for the SAME order must not create N rows
    payload = {"name": "#1001"}
    result = {"missing_skus": ["x"]}
    await note_shopify_result("orders/create", payload, result)
    await note_shopify_result("orders/updated", payload, result)
    body = (await owner_client.get("/api/issues")).json()
    assert body["open_count"] == 1
    assert body["issues"][0]["count"] == 2


async def test_clean_result_records_nothing(owner_client):
    assert await note_shopify_result("orders/create", {"name": "#2"}, {"booked": True, "account_id": 5}) is False
    assert (await owner_client.get("/api/issues")).json()["open_count"] == 0


async def test_partial_warning_on_booked_order(owner_client):
    # the order booked, but a best-effort step (image push) failed → a WARNING, not a refusal
    result = {"status": "ok", "account_id": 9, "warnings": [{"kind": "image_push", "reason": "Files_upload 500"}]}
    assert await note_shopify_result("orders/create", {"name": "#7"}, result) is True
    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "image_push" and iss["severity"] == "warning" and iss["order_ref"] == "#7"


async def test_duplicate_skip_records_nothing(owner_client):
    # idempotency no-op (already booked) must NOT create an issue
    assert await note_shopify_result("orders/updated", {"name": "#8"}, {"skipped": "already seen #8"}) is False
    assert (await owner_client.get("/api/issues")).json()["open_count"] == 0


async def test_needs_review_and_unhandled_topic(owner_client):
    await note_shopify_result("orders/create", {"name": "#3"}, {"status": "needs_review", "reason": "нужен преглед"})
    await note_shopify_result("orders/weird", {"name": "#4"}, {"skipped": "unhandled topic orders/weird"})
    kinds = {i["kind"] for i in (await owner_client.get("/api/issues")).json()["issues"]}
    assert "needs_review" in kinds and "unhandled_topic" in kinds


async def test_resolve_clears_open_count(owner_client):
    await note_shopify_result("orders/create", {"name": "#1001"}, {"missing_skus": ["x"]})
    iid = (await owner_client.get("/api/issues")).json()["issues"][0]["id"]
    assert (await owner_client.post(f"/api/issues/{iid}/resolve")).status_code == 200
    body = (await owner_client.get("/api/issues")).json()
    assert body["open_count"] == 0 and body["issues"][0]["resolved"] is True


async def test_badge_counts_open_issues(owner_client):
    await note_shopify_result("orders/create", {"name": "#1001"}, {"missing_skus": ["x"]})
    assert (await owner_client.get("/api/summary/badges")).json()["/issues"] == 1


async def test_list_requires_auth(client):
    # unauthenticated caller can't read issues
    assert (await client.get("/api/issues")).status_code == 401


async def test_unissued_invoice_gets_its_own_kind_with_the_company_data(owner_client):
    """A B2B фактура the buyer requested but Barsy didn't issue must not vanish — it gets its OWN kind
    carrying the company data, so staff can issue it manually in Barsy (the ЕИК-blocked launch path)."""
    assert await note_shopify_result(
        "orders/create", {"name": "#1010", "id": 1010},
        {"status": "ok", "invoice_requested": True, "invoice_issued": False,
         "invoice": {"company": "АКМЕ ЕООД", "eik": "123456789", "vat": "BG123456789"}}) is True

    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "invoice_not_issued" and iss["order_ref"] == "#1010"
    assert iss["severity"] == "warning" and "ФАКТУРА" in iss["summary"]
    assert "123456789" in str(iss["detail"])          # staff can issue it straight from this


async def test_total_mismatch_is_its_own_kind_with_the_numbers(owner_client):
    """The connector's totals backstop refuses to book when Barsy and Shopify disagree on the money.
    It catches whole CLASSES of pricing bugs (un-booked shipping, pre-discount line prices, tips) that
    nobody anticipated — so it must read as a genuine hold with the figures, not a vague 'needs review'."""
    assert await note_shopify_result(
        "orders/create", {"name": "#1020", "id": 1020},
        {"status": "needs_review", "reason": "total_mismatch",
         "detail": {"booked": 13.50, "shopify_total": 99.00, "difference": -85.50}}) is True

    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "total_mismatch" and iss["severity"] == "error"
    assert "НЕ е осчетоводена" in iss["summary"]          # nothing was booked — say so
    assert iss["detail"]["difference"] == -85.50


# --- reversal guards (storno/refund side) --------------------------------------------
async def test_reversal_mismatch_hard_stops_and_explains_partial_quantity(owner_client):
    """Accounts_storno reverses WHOLE articles, so refunding 1 of 3 units would over-restock by 2 and
    over-reverse the бон. This was a tolerated warning; it's now a refusal — and the message has to say
    WHY, or an operator will just re-run it by hand and cause the same damage."""
    assert await note_shopify_result(
        "refunds/create", {"name": "#1030", "id": 1030},
        {"status": "needs_review", "reason": "reversal_mismatch",
         "detail": {"would_reverse": 13.50, "shopify_refund": 5.00, "difference": 8.50,
                    "suspect": "partial_quantity"}}) is True
    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "reversal_mismatch" and iss["severity"] == "error"
    assert "НЕ е направено" in iss["summary"] and "частично количество" in iss["summary"]


async def test_refund_on_a_non_finalised_order_points_to_voiding_instead(owner_client):
    """At clientorder stage account_id is the CLIENT-ORDER id, not a sale account — a storno would target
    the wrong object. Nothing was decremented either, so the right action is to void, not reverse."""
    assert await note_shopify_result(
        "refunds/create", {"name": "#1031", "id": 1031},
        {"status": "needs_review", "reason": "not_finalised", "detail": {"kind": "clientorder"}}) is True
    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "not_finalised" and iss["severity"] == "warning"
    assert "Откажи клиентската заявка" in iss["summary"]


async def test_unverifiable_reversal_reads_as_a_system_problem(owner_client):
    """A money guard that can't verify must FAIL CLOSED — and the operator must be told it's a system
    fault, not something they did wrong, or they'll "fix" it by reversing manually."""
    assert await note_shopify_result(
        "refunds/create", {"name": "#1032", "id": 1032},
        {"status": "needs_review", "reason": "reversal_unverifiable"}) is True
    iss = (await owner_client.get("/api/issues")).json()["issues"][0]
    assert iss["kind"] == "reversal_unverifiable" and iss["severity"] == "error"
    assert "системен проблем" in iss["summary"]
