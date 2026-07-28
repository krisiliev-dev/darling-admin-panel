# -*- coding: utf-8 -*-
"""Processing-issue recorder — the panel side of "never silently swallow a connector anomaly" (owner
2026-07-27). The webhook receiver already gets the connector's result dict back on every delivery; these
helpers turn any problem it signals (unmatched SKU, needs-review, error, unhandled topic, exception) into
a persisted `ProcessingIssue` that staff SEE (nav badge + Home strip + Проблеми page). Deduped by
(source, kind, order_ref) so a Shopify retry storm bumps a counter instead of spamming N rows."""
import json
import logging

from sqlalchemy import func, select

from .db import async_session_maker
from .models import ProcessingIssue

log = logging.getLogger(__name__)


def _order_ref(payload: dict, result: dict) -> str:
    """A human handle for the order/article this issue is about — Shopify order name (#1001) preferred."""
    if isinstance(result, dict) and result.get("order_ref"):
        return str(result["order_ref"])[:64]
    if isinstance(payload, dict):
        for k in ("name", "order_number", "id"):
            v = payload.get(k)
            if v:
                return (str(v) if str(v).startswith("#") or k != "order_number" else f"#{v}")[:64]
    return ""


async def record_issue(*, source: str, topic: str, order_ref: str, severity: str, kind: str,
                       summary: str, detail: dict | None = None) -> None:
    """Upsert an anomaly. An UNRESOLVED issue with the same (source, kind, order_ref) is bumped
    (count += 1, last_seen refreshed) rather than duplicated. Never raises — issue-logging must never
    break the (already-200'd) webhook handler."""
    try:
        async with async_session_maker() as session:
            existing = (await session.execute(
                select(ProcessingIssue).where(
                    ProcessingIssue.resolved.is_(False),
                    ProcessingIssue.source == source,
                    ProcessingIssue.kind == kind,
                    ProcessingIssue.order_ref == order_ref,
                ))).scalars().first()
            body = json.dumps(detail or {}, ensure_ascii=False, default=str)[:8000]
            if existing:
                existing.count += 1
                existing.last_seen = func.now()
                existing.summary = summary[:500]
                existing.detail = body
                existing.severity = severity
            else:
                session.add(ProcessingIssue(
                    source=source, topic=topic, order_ref=order_ref, severity=severity,
                    kind=kind, summary=summary[:500], detail=body))
            await session.commit()
        log.warning("processing issue [%s/%s] %s: %s", source, kind, order_ref, summary)
    except Exception:  # noqa: BLE001 — recording an issue must never itself take down the handler
        log.exception("failed to record processing issue (%s/%s)", source, kind)


async def note_shopify_result(topic: str, payload: dict, result: dict | None) -> bool:
    """Inspect a connector result for a Shopify webhook and record an issue if it signals a problem.
    Returns True if an issue was recorded. Recognises both the current result keys (`missing_skus`,
    `needs_review`, `error`, `skipped`) and the richer `{status, reason, detail}` contract the connector
    is moving to. A clean 'processed' result records nothing."""
    result = result or {}
    ref = _order_ref(payload, result)
    status = str(result.get("status") or "").lower()

    # unmatched SKU(s) — the #1001 case: line item(s) with no matching Barsy article
    missing = result.get("missing_skus") or result.get("unmatched_skus")
    if missing:
        await record_issue(
            source="shopify", topic=topic, order_ref=ref, severity="error", kind="unmatched_sku",
            summary=f"Поръчка {ref or '(без номер)'} не е осчетоводена — {len(missing)} артикул(а) без "
                    f"съответствие по SKU в Barsy.",
            detail={"missing_skus": missing, "reason": result.get("reason"), "topic": topic})
        return True

    # A requested фактура (B2B VAT invoice) that Barsy did NOT issue must never just vanish — the buyer
    # asked for it at checkout and is legally owed one. Give it its own kind so staff can spot it and
    # issue it manually in Barsy from the captured company data (the detail carries name/ЕИК/ДДС/адрес).
    reason_txt = str(result.get("reason") or result.get("error") or "").lower()
    if result.get("invoice_requested") and not result.get("invoice_issued") or "invoice" in reason_txt \
            or "фактур" in reason_txt:
        inv = result.get("invoice") or result.get("invoice_data") or {}
        await record_issue(
            source="shopify", topic=topic, order_ref=ref, severity="warning", kind="invoice_not_issued",
            summary=f"Поръчка {ref or '(без номер)'} — клиентът е поискал ФАКТУРА, но тя не е издадена. "
                    f"Издай я ръчно в Barsy по данните тук.",
            detail={"invoice": inv, "reason": result.get("reason") or result.get("error"),
                    "result": {k: v for k, v in result.items() if k != "lines"}, "topic": topic})
        return True

    # explicit refusal / needs-review from the connector
    if status in ("refused", "needs_review", "error") or result.get("needs_review") or result.get("error"):
        kind = "error" if (status == "error" or result.get("error")) else "needs_review"
        sev = "error" if kind == "error" else "warning"
        reason = result.get("reason") or result.get("error") or result.get("note") or status or "нужен преглед"
        await record_issue(
            source="shopify", topic=topic, order_ref=ref, severity=sev, kind=kind,
            summary=f"Поръчка {ref or '(без номер)'} — {reason}",
            detail={"result": {k: v for k, v in result.items() if k != "lines"}, "topic": topic})
        return True

    # partial warnings on an otherwise-booked order — a best-effort step failed (image push, metafield
    # upsert…) but the sale itself went through. Record each as a WARNING (not a refusal), keyed by kind.
    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        for w in warnings:
            if not isinstance(w, dict):
                continue
            wkind = str(w.get("kind") or "warning")[:48]
            await record_issue(
                source="shopify", topic=topic, order_ref=ref, severity="warning", kind=wkind,
                summary=f"{ref or 'Поръчка'}: {w.get('reason') or wkind}",
                detail={"warning": w, "topic": topic})
        return True

    # a topic we received but don't translate — worth surfacing (a subscription we don't handle)
    skipped = str(result.get("skipped") or "")
    if skipped.startswith("unhandled topic"):
        await record_issue(
            source="shopify", topic=topic, order_ref=ref, severity="warning", kind="unhandled_topic",
            summary=f"Необработена Shopify тема: {topic}",
            detail={"topic": topic, "skipped": skipped})
        return True
    return False


async def note_exception(source: str, topic: str, payload: dict, err: Exception) -> None:
    """A handler threw — record it as an error issue (the loudest signal that something's wrong)."""
    ref = _order_ref(payload if isinstance(payload, dict) else {}, {})
    await record_issue(
        source=source, topic=topic, order_ref=ref, severity="error", kind="exception",
        summary=f"Грешка при обработка на {topic} {ref}: {str(err)[:200]}",
        detail={"error": str(err)[:1000], "topic": topic})
