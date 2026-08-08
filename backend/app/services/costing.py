# -*- coding: utf-8 -*-
"""Invoice-line cost arithmetic — store what is PRINTED, derive the rest.

A single collapsed `price` column loses the information needed to check it later. The supplier's
line carries: a unit price, whether that price already includes VAT, a ДДС group, a discount on the
line, a discount on the whole document, and a currency with a rate. Barsy's own Ново зареждане
screen exposes every one of them, and its tooltip on the VAT toggle warns — in their words — that
getting it wrong makes себестойността compute incorrectly. Silently.

So: keep the printed values, derive net unit cost from them, and keep a rounding residual so the
derived lines add up to the invoice total EXACTLY. Without the residual a few stotinki of drift per
invoice makes a rounding artefact indistinguishable from a real discrepancy, and then nobody trusts
either.

Decimal throughout, never float: 0.1 + 0.2 is not 0.3, and this arithmetic ends up in stock
valuation. Rounding is ROUND_HALF_UP, which is what an invoice does and what a human checking the
sum by hand will expect.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Barsy ДДС groups, read off the live Ново зареждане screen 2026-08-07.
# id -> rate. Two groups are 20% (Б and В) and two are 0% (А and „Без ДДС") — they differ in
# REPORTING, not in arithmetic, so both map to the same rate here and the id is kept on the line.
VAT_GROUPS: dict[int, Decimal] = {
    1: Decimal("0"),        # BG 0% (А)
    2: Decimal("0.20"),     # BG 20% (Б)   <- the default
    3: Decimal("0.20"),     # BG 20% (В)
    4: Decimal("0.09"),     # BG 9% (Г)
    12: Decimal("0.07"),    # BG 7%
    100: Decimal("0"),      # BG 0% (Без ДДС)
}

# Money is rounded to 2 places; a UNIT cost keeps 4, because dividing a line total by a quantity
# legitimately produces sub-stotinka precision that must not be thrown away before it is multiplied
# back up (a 0.005 error on a unit cost becomes 0.36 on a case of 72).
MONEY = Decimal("0.01")
UNIT = Decimal("0.0001")


def dec(v, default="0") -> Decimal:
    """Anything a sheet or a JSON body can hand us → Decimal. Never raises: a malformed cell must
    not take down a delivery, it must land as zero and be visible as such."""
    if isinstance(v, Decimal):
        return v
    try:
        s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
        return Decimal(s) if s else Decimal(default)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def vat_rate(group_id) -> Decimal:
    """The rate for a Barsy ДДС group. An unknown group is treated as 0 rather than guessed at 20% —
    inventing tax is worse than under-deriving it, and a zero is visible in a reconciliation."""
    try:
        return VAT_GROUPS.get(int(group_id), Decimal("0"))
    except (TypeError, ValueError):
        return Decimal("0")


def net_unit_price(printed_price, *, includes_vat: bool, vat_group_id=2,
                   line_discount_pct=0, doc_discount_pct=0, fx_rate=1) -> Decimal:
    """Net unit price in SYSTEM currency, from the line exactly as printed.

    Order matters and is the invoice's own: strip VAT first (it is charged on the discounted net,
    so a VAT-inclusive price must be un-grossed before discounts are applied), then the line
    discount, then the document discount, then convert. Doing discounts before stripping VAT
    produces a different number — a small one per unit, a large one per pallet.
    """
    p = dec(printed_price)
    if p <= 0:
        return Decimal("0")
    if includes_vat:
        p = p / (Decimal("1") + vat_rate(vat_group_id))
    for pct in (dec(line_discount_pct), dec(doc_discount_pct)):
        if pct:
            p = p * (Decimal("1") - pct / Decimal("100"))
    p = p * dec(fx_rate, "1")
    return p.quantize(UNIT, rounding=ROUND_HALF_UP)


def line_net_total(qty, printed_price, **kw) -> Decimal:
    """Net total for a line, rounded to money. Rounding happens ONCE, at the total — rounding the
    unit price to stotinki first and multiplying would drift by up to half a stotinka per unit."""
    return (dec(qty) * net_unit_price(printed_price, **kw)).quantize(
        MONEY, rounding=ROUND_HALF_UP)


def line_vat(qty, printed_price, *, vat_group_id=2, **kw) -> Decimal:
    net = line_net_total(qty, printed_price, vat_group_id=vat_group_id, **kw)
    return (net * vat_rate(vat_group_id)).quantize(MONEY, rounding=ROUND_HALF_UP)


def reconcile(lines: list[dict], invoice_total, *, with_vat: bool = True) -> dict:
    """Do the derived lines add up to what the invoice says?

    `lines` are dicts of the printed values (qty, printed_price, includes_vat, vat_group_id,
    line_discount_pct, doc_discount_pct, fx_rate). Returns the derived totals and the RESIDUAL —
    derived minus printed. A residual of a stotinka or two is rounding; a larger one is a real
    discrepancy worth a human's attention, and keeping the number is what lets anyone tell them
    apart instead of arguing about it."""
    net = Decimal("0")
    vat = Decimal("0")
    for ln in lines:
        kw = {
            "includes_vat": bool(ln.get("includes_vat")),
            "vat_group_id": ln.get("vat_group_id", 2),
            "line_discount_pct": ln.get("line_discount_pct", 0),
            "doc_discount_pct": ln.get("doc_discount_pct", 0),
            "fx_rate": ln.get("fx_rate", 1),
        }
        net += line_net_total(ln.get("qty", 0), ln.get("printed_price", 0), **kw)
        vat += line_vat(ln.get("qty", 0), ln.get("printed_price", 0), **kw)
    net = net.quantize(MONEY, rounding=ROUND_HALF_UP)
    vat = vat.quantize(MONEY, rounding=ROUND_HALF_UP)
    gross = (net + vat).quantize(MONEY, rounding=ROUND_HALF_UP)
    printed = dec(invoice_total)
    derived = gross if with_vat else net
    residual = (derived - printed).quantize(MONEY, rounding=ROUND_HALF_UP)
    return {
        "net": net, "vat": vat, "gross": gross,
        "invoice_total": printed, "residual": residual,
        # a stotinka either way is arithmetic; more than that is somebody's mistake
        "reconciles": abs(residual) <= Decimal("0.01"),
    }


def barsy_unit_price(printed_price, *, includes_vat: bool, vat_group_id=2,
                     line_discount_pct=0, doc_discount_pct=0, fx_rate=1,
                     barsy_expects_vat_inclusive: bool = True) -> Decimal:
    """The unit price to SEND to Barsy — discounted and converted, with VAT put back if Barsy is
    expecting a gross price.

    This exists because of a trap worth spelling out. `barsy_price_mode = 1` means "the unit prices
    we send are VAT-INCLUSIVE", and Barsy strips the VAT itself to compute себестойност (polarity
    confirmed in their storeloads/getlist docs: 0 => без включено ДДС, 1 => с включено ДДС). So
    handing Barsy a NET price while still declaring mode 1 would make it strip VAT a second time and
    understate cost by ~17% — silently, which is the exact failure this whole slice exists to
    prevent.

    net_unit_price() stays the single source of truth for the arithmetic; this only re-grosses at
    the end when the ERP contract calls for a gross number. Set
    barsy_expects_vat_inclusive=False if price_mode is ever flipped to 0.

    What genuinely changes versus what we sent before: the printed price used to go through raw, so
    a line discount, a document discount and a currency rate were all ignored. Those are applied
    now. VAT handling stays exactly where it already worked."""
    net = net_unit_price(printed_price, includes_vat=includes_vat, vat_group_id=vat_group_id,
                         line_discount_pct=line_discount_pct, doc_discount_pct=doc_discount_pct,
                         fx_rate=fx_rate)
    if not barsy_expects_vat_inclusive:
        return net
    return (net * (Decimal("1") + vat_rate(vat_group_id))).quantize(
        UNIT, rounding=ROUND_HALF_UP)
