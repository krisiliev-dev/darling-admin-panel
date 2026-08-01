# -*- coding: utf-8 -*-
"""Zero-price go-live guard (owner invariant, 2026-08-02): NO product may reach the
website — or the till — priced 0. Fail closed on money: THREE states, never a boolean.

Barsy has no `is_active`. The owner-verified flag mapping (28.07, connector's
barsy_shopify_sync) is: `is_for_sale=0` pulls an article from BOTH the online store and
the POS; `is_public=0` alone only hides it online while leaving it sellable in-store.
The invariant demands the former — a zero-price product is forced to is_for_sale=0
(and is_public=0, so a partial Barsy read can never resurrect it online by default).

Every push path that ever sends an article outward (Barsy upload, Woo catalogue import,
the Phase C worker) MUST route its payload through `apply_to_article()`. The publish
audit (#81) REPORTS zero-price products; this module ENFORCES the invariant at the
moment of push. Layer three (post-push price read-back) lives with the push code —
each layer tests the RESULT, not a flag."""
from __future__ import annotations

OK = "ok"                            # price > 0 — payload passes untouched
FORCED_INACTIVE = "forced_inactive"  # price parseable and <= 0 — forced off sale
BLOCKED = "blocked"                  # price missing/unreadable — REFUSE to push at all


def gate(price) -> dict:
    """Classify a price into the three guard states. Accepts int/float/Decimal/str
    (comma decimals like '12,50' included). None / '' / unparseable → BLOCKED —
    an unknown price is never presumed sellable."""
    if price is None or (isinstance(price, str) and not price.strip()):
        return {"state": BLOCKED, "reason": "липсва цена"}
    try:
        v = float(str(price).replace(",", ".").strip())
    except (ValueError, TypeError):
        return {"state": BLOCKED, "reason": f"нечетима цена: {str(price)[:40]!r}"}
    if v > 0:
        return {"state": OK, "price": round(v, 2)}
    return {"state": FORCED_INACTIVE, "price": round(v, 2),
            "reason": "цена <= 0 — принудително is_for_sale=0, is_public=0"}


def apply_to_article(article: dict, price) -> tuple[dict | None, dict]:
    """Return (payload_to_push, verdict).

    OK              → the article, unchanged.
    FORCED_INACTIVE → a COPY with is_for_sale=0 AND is_public=0 baked in.
    BLOCKED         → payload is **None** — deliberately not a dict, so a caller that
                      ignores the verdict crashes on push instead of pushing garbage.
                      (A guard that degrades to a falsy-but-usable value is not a guard.)

    The verdict travels with the payload so callers can SURFACE a forced-off product —
    a silently deactivated article is the next 'защо този продукт го няма' mystery."""
    verdict = gate(price)
    if verdict["state"] == OK:
        return article, verdict
    if verdict["state"] == FORCED_INACTIVE:
        out = dict(article)
        out["is_for_sale"] = 0
        out["is_public"] = 0
        return out, verdict
    return None, verdict
