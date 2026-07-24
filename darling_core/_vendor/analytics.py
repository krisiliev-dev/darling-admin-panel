# -*- coding: utf-8 -*-
"""Analytics data model + ETL for the ops console and Metabase (owner 2026-07-20).

The insights the owner wants (top sellers, revenue, budget allocation, supplier discount
windows, supplier price trends) span THREE sources no single BI tool combines: Shopify
(sales), Barsy (costs), and our own price history. So we build a small star schema here and
let Metabase draw the charts.

Tables (all in pipeline.db, so Metabase points at ONE file):
  dim_product(code, name, brand, category_leaf, supplier)
  price_history(snapshot_date, code, retail_price, cost_price, supplier)   ← accrues over time
  fact_sales(order_id, order_name, ts, code, qty, unit_price, revenue)      ← from Shopify
  fact_purchases(ts, code, qty, unit_cost, supplier, ref)                   ← from Barsy

price_history is the differentiator: snapshot_prices() records today's prices, so trends and
discount windows emerge as it runs daily. Sales/purchases populate as the store goes live.
"""
import datetime
import json

import db


def init_schema(con=None):
    c = con or db.connect()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS dim_product(
            code TEXT PRIMARY KEY, name TEXT, brand TEXT, category_leaf TEXT, supplier TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS price_history(
            snapshot_date TEXT, code TEXT, retail_price REAL, cost_price REAL, supplier TEXT,
            PRIMARY KEY(snapshot_date, code))""")
        c.execute("""CREATE TABLE IF NOT EXISTS fact_sales(
            order_id TEXT, order_name TEXT, ts TEXT, code TEXT, qty INTEGER,
            unit_price REAL, revenue REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_sales_code ON fact_sales(code)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_sales_ts ON fact_sales(ts)")
        c.execute("""CREATE TABLE IF NOT EXISTS fact_purchases(
            ts TEXT, code TEXT, qty INTEGER, unit_cost REAL, supplier TEXT, ref TEXT)""")
        c.commit()
    finally:
        if con is None:
            c.close()


_TASK_IDX = None


def _task_index():
    """{code: {'price': float|None, 'supplier': str}} from the Microinvest task store — the
    authoritative source for retail price (product.Цена) and supplier (ground_truth.Група).
    Cached per process."""
    global _TASK_IDX
    if _TASK_IDX is None:
        try:
            import barsy_suppliers as bs
            clean = bs._clean_name
        except Exception:
            def clean(g):
                return (str(g or "").split("/", 1)[-1].strip() or "").upper()
        out = {}
        c = db.connect()
        try:
            for code, tj in c.execute("SELECT code, task_json FROM tasks").fetchall():
                try:
                    t = json.loads(tj or "{}")
                except Exception:
                    continue
                mi = t.get("product") or {}
                gt = t.get("ground_truth") or {}
                out[str(code)] = {"price": _num(mi.get("Цена")),
                                  "supplier": clean(gt.get("Група")) if gt.get("Група") else ""}
        finally:
            c.close()
        _TASK_IDX = out
    return _TASK_IDX


def refresh_dim_product(con=None):
    """Rebuild dim_product from the live catalog (name/brand/category/supplier per code)."""
    c = con or db.connect()
    init_schema(c)
    try:
        import product_taxonomy  # noqa
    except Exception:
        pass
    tix = _task_index()
    try:
        leaf = {code: full for code, full in
                c.execute("SELECT code, full_name FROM product_taxonomy").fetchall()}
        rows = []
        for code, fj in c.execute("SELECT code, fields_json FROM products").fetchall():
            f = json.loads(fj or "{}")
            rows.append((str(code), f.get("Наименование") or "", f.get("Бранд") or "",
                         leaf.get(str(code), ""), (tix.get(str(code)) or {}).get("supplier", "")))
        c.executemany("INSERT OR REPLACE INTO dim_product VALUES(?,?,?,?,?)", rows)
        c.commit()
        return len(rows)
    finally:
        if con is None:
            c.close()


def snapshot_prices(con=None):
    """Record TODAY's price for every product → price_history (idempotent per day). This is
    what makes trend + discount-window charts possible; run it daily (cron)."""
    c = con or db.connect()
    init_schema(c)
    today = datetime.date.today().isoformat()
    tix = _task_index()
    try:
        rows = []
        for code, fj in c.execute("SELECT code, fields_json FROM products").fetchall():
            f = json.loads(fj or "{}")
            ti = tix.get(str(code)) or {}
            price = _num(f.get("Цена")) or ti.get("price")     # enriched, else Microinvest
            cost = _num(f.get("Доставна цена") or f.get("Себестойност"))
            if price is None and cost is None:
                continue
            rows.append((today, str(code), price, cost, ti.get("supplier", "")))
        c.executemany("INSERT OR REPLACE INTO price_history"
                      "(snapshot_date, code, retail_price, cost_price, supplier) VALUES(?,?,?,?,?)", rows)
        c.commit()
        return len(rows)
    finally:
        if con is None:
            c.close()


def ingest_shopify_sales():
    """Pull Shopify orders → fact_sales (append-only; skips already-loaded order ids). Safe to
    run on a schedule once the store is live."""
    import shopify_orders as so
    c = db.connect()
    init_schema(c)
    try:
        have = {r[0] for r in c.execute("SELECT DISTINCT order_id FROM fact_sales").fetchall()}
        n = 0
        for o in so.fetch_orders(limit=250):
            if o["id"] in have:
                continue
            for it in o["items"]:
                code = str(it.get("sku") or "")
                unit = (o["total"] / max(1, sum(x["qty"] for x in o["items"]))) if o["items"] else 0
                c.execute("INSERT INTO fact_sales VALUES(?,?,?,?,?,?,?)",
                          (o["id"], o["name"], o["created"], code, it["qty"], unit,
                           unit * it["qty"]))
                n += 1
        c.commit()
        return n
    finally:
        c.close()


# ---------------------------------------------------------------- KPI queries ----------
def kpis():
    """At-a-glance numbers for the ops-console dashboard."""
    c = db.connect()
    init_schema(c)
    try:
        def one(q):
            try:
                return c.execute(q).fetchone()[0] or 0
            except Exception:
                return 0
        today = datetime.date.today().isoformat()
        return {
            "products": one("SELECT COUNT(*) FROM dim_product"),
            "revenue_total": round(one("SELECT SUM(revenue) FROM fact_sales"), 2),
            "revenue_today": round(one(f"SELECT SUM(revenue) FROM fact_sales WHERE ts>='{today}'"), 2),
            "orders_total": one("SELECT COUNT(DISTINCT order_id) FROM fact_sales"),
            "units_total": one("SELECT SUM(qty) FROM fact_sales"),
            "suppliers": one("SELECT COUNT(DISTINCT supplier) FROM dim_product WHERE supplier<>''"),
            "price_snapshots": one("SELECT COUNT(DISTINCT snapshot_date) FROM price_history"),
            "avg_retail": round(one("SELECT AVG(retail_price) FROM price_history "
                                    f"WHERE snapshot_date=(SELECT MAX(snapshot_date) FROM price_history)"), 2),
        }
    finally:
        c.close()


def top_products(by="revenue", limit=10):
    c = db.connect()
    try:
        col = "SUM(revenue)" if by == "revenue" else "SUM(qty)"
        rows = c.execute(f"SELECT s.code, d.name, {col} v FROM fact_sales s "
                         "LEFT JOIN dim_product d ON s.code=d.code "
                         f"GROUP BY s.code ORDER BY v DESC LIMIT {int(limit)}").fetchall()
        return [{"code": r[0], "name": r[1] or r[0], "value": round(r[2] or 0, 2)} for r in rows]
    finally:
        c.close()


def price_changes(days=90, min_pct=5.0):
    """Products whose retail price moved between the two most recent snapshots — surfaces
    supplier discount windows / increases. Empty until >=2 snapshots exist."""
    c = db.connect()
    try:
        dates = [r[0] for r in c.execute(
            "SELECT DISTINCT snapshot_date FROM price_history ORDER BY snapshot_date DESC LIMIT 2").fetchall()]
        if len(dates) < 2:
            return []
        new, old = dates[0], dates[1]
        rows = c.execute(
            "SELECT n.code, d.name, o.retail_price, n.retail_price, n.supplier "
            "FROM price_history n JOIN price_history o ON n.code=o.code "
            "LEFT JOIN dim_product d ON n.code=d.code "
            "WHERE n.snapshot_date=? AND o.snapshot_date=? AND o.retail_price>0 "
            "AND n.retail_price<>o.retail_price", (new, old)).fetchall()
        out = []
        for code, name, op, np_, sup in rows:
            pct = round(100.0 * (np_ - op) / op, 1)
            if abs(pct) >= min_pct:
                out.append({"code": code, "name": name or code, "old": op, "new": np_,
                            "pct": pct, "supplier": sup, "direction": "↓ отстъпка" if pct < 0 else "↑ вдигане"})
        out.sort(key=lambda x: x["pct"])
        return out
    finally:
        c.close()


def _num(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if "--snapshot" in sys.argv:
        print("dim_product:", refresh_dim_product())
        print("price snapshot rows:", snapshot_prices())
    elif "--sales" in sys.argv:
        print("sales ingested:", ingest_shopify_sales())
    else:
        print("kpis:", kpis())
