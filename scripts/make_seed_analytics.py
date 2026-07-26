# -*- coding: utf-8 -*-
"""Bake a lean HISTORICAL seed from the old-ERP Microinvest analytics export (data2.json) into the
panel so the pre-migration business diagnosis ships with the app (data2.json lives only on the
Desktop). We keep KPIs + trend + ABC + top suppliers + top dead products — NOT all 12k rows.

CURRENCY: the export values are ALREADY IN EUR (owner-confirmed) — no conversion is applied. This
is historical data for reference only; the LIVE panel view is Barsy, which takes over as the store
accrues its own history. Coverage: the export's monthly series, ~2021-11 .. 2026-07.
"""
import json
import os

SRC = r"C:\Users\lora_\Desktop\barsy_import\data2.json"
DST = r"C:\Users\lora_\Desktop\product-review\admin-panel\backend\app\data\seed_analytics.json"


def num(v):
    """Pass values through as-is (already EUR); just coerce to a rounded float."""
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return v


d = json.load(open(SRC, encoding="utf-8"))
kpi = d["kpi"]
C = d["cols"]  # name,sup,units,rev,marg,stock,stockval,last,flag,cover,price,yr
i = {c: n for n, c in enumerate(C)}
months = sorted(d["ts"].keys())

# dead products (flag bit 2) → top 50 by capital tied up (stockval)
dead = [p for p in d["P"] if (int(p[i["flag"]]) & 2) and float(p[i["stockval"]] or 0) > 0]
dead.sort(key=lambda p: float(p[i["stockval"]] or 0), reverse=True)
dead_top = [{
    "name": p[i["name"]], "supplier": p[i["sup"]] or "",
    "stock": p[i["stock"]], "stockval": num(p[i["stockval"]]),
    "cover": p[i["cover"]], "last": p[i["last"]],
    "discontinued": bool(int(p[i["flag"]]) & 1),
} for p in dead[:50]]

seed = {
    "meta": {
        "maxdate": d["maxdate"], "currency": "EUR",
        "period_from": months[0] if months else None, "period_to": months[-1] if months else None,
        "source": "Стар ERP (Microinvest) · историческа снимка",
        "note": "Данни само за справка от стария софтуер. Живите числа идват от Barsy.",
        "products_total": len(d["P"]), "dead_total": len(dead),
    },
    "kpi": {
        "rev": num(kpi["rev"]), "gm": kpi["gm"], "inv": num(kpi["inv"]),
        "turnover": kpi["turnover"], "gmroi": kpi["gmroi"], "dsi": kpi["dsi"],
        # dead_n = our own recount (same stockval>0 filter as dead_total / the dead list), not the
        # upstream KPI's black-box count — keeps the displayed count consistent with the dead basis.
        "dead_val": num(kpi["dead_val"]), "dead_pct": kpi["dead_pct"], "dead_n": len(dead),
        "active": kpi["active"], "reorder": kpi["reorder"], "units": kpi["units"],
    },
    "rev_year": {y: num(v) for y, v in d["rev_year"].items()},
    "ts": {m: {"rev": num(d["ts"][m].get("rev")), "cogs": num(d["ts"][m].get("cogs")),
               "purch": num(d["ts"][m].get("purch")), "units": d["ts"][m].get("units")}
           for m in months},
    "abc": {k: {"count": v[0], "value": num(v[1])} for k, v in d["abc"].items()},
    "suppliers": sorted(
        [{"name": s["name"], "rev": num(s["rev"]), "marg": s.get("marg"),
          "nprod": s.get("nprod"), "deadval": num(s.get("deadval"))} for s in d["suppliers"]],
        key=lambda s: s["rev"], reverse=True),   # all suppliers (sortable/filterable in the UI)
    "dead_products": dead_top,
}

os.makedirs(os.path.dirname(DST), exist_ok=True)
json.dump(seed, open(DST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("wrote", DST, os.path.getsize(DST), "bytes")

# --- product explorer dataset (ALL products, trimmed) → separate (bigger) file, served + filtered
# server-side so we never ship 12k rows to the browser at once ---
PRODUCTS_DST = DST.replace("seed_analytics.json", "seed_products.json")
products = [{
    "code": str(p[i["code"]]) if i.get("code") is not None and p[i["code"]] is not None else "",
    "name": p[i["name"]], "supplier": p[i["sup"]] or "",
    "units": p[i["units"]], "rev": num(p[i["rev"]]), "marg": p[i["marg"]],
    "stock": p[i["stock"]], "stockval": num(p[i["stockval"]]),
    "price": num(p[i["price"]]) if i.get("price") is not None else None,
    "cost": num(p[i["cost"]]) if i.get("cost") is not None else None,
    "last": p[i["last"]], "cover": p[i["cover"]],
    "dead": bool(int(p[i["flag"]]) & 2), "discontinued": bool(int(p[i["flag"]]) & 1),
} for p in d["P"]]
json.dump({"meta": seed["meta"], "products": products},
          open(PRODUCTS_DST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("wrote", PRODUCTS_DST, os.path.getsize(PRODUCTS_DST), "bytes,", len(products), "products")
print("period:", seed["meta"]["period_from"], "..", seed["meta"]["period_to"])
print("kpi(EUR, as-is):", json.dumps(seed["kpi"], ensure_ascii=False))
print("dead_top[0]:", json.dumps(dead_top[0], ensure_ascii=False))
