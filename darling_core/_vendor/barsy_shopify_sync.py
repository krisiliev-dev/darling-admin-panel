# -*- coding: utf-8 -*-
"""Barsy → Shopify product sync (owner 2026-07-19).

Barsy is the system of record; Shopify is the storefront. This upserts a product into
Shopify from its enriched record, keyed by our Microinvest `code` (= Shopify variant SKU,
so the sync is idempotent). Price authority = Barsy `actual_price` when the article exists,
else the Microinvest export. All the rich content (name, description, brand, Shopify-taxonomy
category, synonyms→tags, images) comes from our enriched DB record.

Products are created as **DRAFT** (never auto-published). Per the migration plan we also push
the product to the Cloudflare semantic-search worker on every upsert.

  python barsy_shopify_sync.py plan <code>              # show the mapped Shopify input, no write
  python barsy_shopify_sync.py sync <code> --confirm     # upsert one product (DRAFT)
  python barsy_shopify_sync.py delete-test <product_id> --confirm
"""
import argparse
import json
import re
import sys
import urllib.request

import shopify_api as s


def _staged_upload(filename, data, mime="image/png"):
    """Push raw image BYTES to Shopify via a staged upload and return the resourceUrl to hand to
    productCreateMedia. Lets us publish our LOCAL processed cutouts directly — no public URL
    needed (Barsy's own picture URL is auth-walled / 401, so Shopify can't fetch it)."""
    q = """mutation($input:[StagedUploadInput!]!){
      stagedUploadsCreate(input:$input){
        stagedTargets{ url resourceUrl parameters{ name value } }
        userErrors{ field message } } }"""
    tgt = s.gql(q, {"input": [{"filename": filename, "mimeType": mime,
                               "resource": "IMAGE", "httpMethod": "POST"}]})
    t = tgt["stagedUploadsCreate"]["stagedTargets"][0]
    boundary = "----darlingprocimg7f3a"
    body = b""
    for p in t["parameters"]:                       # signed params MUST precede the file part
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"{p['name']}\"\r\n\r\n{p['value']}\r\n").encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode("utf-8") + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(t["url"], data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=90).read()
    return t["resourceUrl"]

# our Bulgarian dept keyword -> Shopify auto-collection tag (from the storefront's 11 collections)
DEPT_TAGS = {
    "кухн": "кухня", "бан": "баня", "декор": "декорация", "текстил": "текстил",
    "градин": "градина", "почист": "почистване", "парти": "парти", "дет": "деца",
    "подар": "подаръци",
}


def _barsy_price(code):
    """Storefront price for an enriched product = the article's ONLINE price: current_price under
    the "address"/online delivery channel (reflects any active Barsy Pricelists rule scoped to
    online) with a fallback to base actual_price. Prices come from Barsy/Microinvest, never
    AI-touched — this only picks the online channel's price. No-op until channel pricing is set."""
    try:
        import barsy_api as b
        for a in b.call("Articles_getlist", {"filters": {"delivery_type_id": "address"},
                                             "extra_properties": {"current_price": 1}}):
            if str(a.get("code")) == str(code):
                cp = a.get("current_price")
                return str(cp if cp not in (None, "") else (a.get("actual_price") or ""))
    except Exception:
        pass
    return ""


def _parse_weight_kg(raw):
    """Barsy `extra_data.weight_kg` (string, may be comma-decimal in BG locale) → positive float KG,
    or None if missing/unparseable/≤0. Per spec: NEVER push 0 — an unset weight is better than a
    weightless parcel (0 would make the courier quote think the parcel has no weight)."""
    if raw in (None, ""):
        return None
    try:
        v = float(str(raw).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _weight_input(raw):
    """→ Shopify InventoryItemInput.measurement dict for a weight, or None to omit the field entirely.
    Stored as KG/KILOGRAMS; Shopify still exposes it as item.grams (kg×1000) in /cart.js."""
    v = _parse_weight_kg(raw)
    return {"weight": {"value": v, "unit": "KILOGRAMS"}} if v is not None else None


def _barsy_weight_kg(code):
    """Fetch a single article's parcel weight (kg) from Barsy extra_data.weight_kg. Read via
    extra_properties:{extra_data:1} (⚠ extra_properties:{weight:1} 500s)."""
    try:
        import barsy_api as b
        for a in b.call("Articles_getlist", {"filters": {}, "extra_properties": {"extra_data": 1}}):
            if str(a.get("code")) == str(code):
                ed = a.get("extra_data")
                return _parse_weight_kg((ed or {}).get("weight_kg") if isinstance(ed, dict) else None)
    except Exception:
        pass
    return None


def _barsy_flags(code):
    """Read a Barsy article's status flags via getlist (reliable — Articles_get returns null for these
    in some contexts). Returns {is_for_sale, is_public, is_service, delete_flag} (values as-is)."""
    try:
        import barsy_api as b
        for a in b.call("Articles_getlist", {"filters": {}}):
            if str(a.get("code")) == str(code):
                return {k: a.get(k) for k in ("is_for_sale", "is_public", "is_service", "delete_flag")}
    except Exception:
        pass
    return {}


def shopify_status(flags):
    """Barsy article flags → Shopify product status (owner mapping 2026-07-28):
      • is_service=1 OR delete_flag=1 OR is_for_sale=0 → None  = NOT on Shopify (skip-create / archive-if-exists)
      • is_for_sale=1 AND is_public=0 → 'DRAFT'   (sellable in-store, hidden from the online store)
      • is_for_sale=1 AND is_public=1 → 'ACTIVE'  (live on the online store)
    An absent/None flag takes its permissive default (for_sale/public=1, service/deleted=0) so a partial
    record never silently hides a product."""
    def on(k, default):
        v = flags.get(k)
        return (str(v) == "1") if v is not None else default
    if on("is_service", False) or on("delete_flag", False):
        return None
    if not on("is_for_sale", True):
        return None
    return "ACTIVE" if on("is_public", True) else "DRAFT"


def _fields(code):
    import db
    rec = db.get_record(code) or {}
    return dict(rec.get("fields") or {}), (rec.get("meta") or {})


def _tags(fields, leaf_full):
    tags = set()
    hay = (leaf_full + " " + str(fields.get("Категории") or "")).lower()
    for kw, tag in DEPT_TAGS.items():
        if kw in hay:
            tags.add(tag)
    for syn in re.split(r"[,;]", str(fields.get("Синоними") or "")):
        syn = syn.strip()
        if 2 <= len(syn) <= 40:
            tags.add(syn)
    return sorted(tags)


def build_input(code):
    """Map our enriched record + Barsy price -> Shopify ProductSetInput (minus variants/media,
    which the sync attaches after create). Returns (product_input, price, barcode, images)."""
    fields, meta = _fields(code)
    name = str(fields.get("Наименование") or "").strip()
    if not name:
        raise s.ShopifyError(f"{code}: no enriched name")
    leaf_gid = meta.get("leaf_gid")
    price = _barsy_price(code)
    if not price:
        try:
            import barsy_collect as bcol
            price = (bcol._export_price_qty().get(code) or {}).get("price") or ""
        except Exception:
            price = ""
    barcodes = []
    try:
        import ean_brand as eb
        barcodes = [d["ean"] for d in eb._load(eb.BARCODES)
                    if d.get("cls") == "gs1" and str(d["code"]) == str(code)]
    except Exception:
        pass
    images = [u.strip() for u in re.split(r"[,\s]+", str(fields.get("Снимки") or ""))
              if u.strip().startswith("http")]
    status = shopify_status(_barsy_flags(code))    # ACTIVE/DRAFT from Barsy is_for_sale/is_public, or None
    pin = {
        "title": name,
        "descriptionHtml": str(fields.get("Описание") or ""),
        "vendor": str(fields.get("Бранд") or "").strip() or "Darling",
        "productType": meta.get("leaf_name") or "",
        "status": status or "DRAFT",               # sync_code archives/skips instead when status is None
        "tags": _tags(fields, meta.get("leaf_full") or ""),
    }
    if leaf_gid:
        pin["category"] = leaf_gid          # Shopify taxonomy gid == our leaf_gid
    seo_t = str(fields.get("Мета наименование") or "").strip()
    seo_d = str(fields.get("Мета описание") or "").strip()
    if seo_t or seo_d:
        pin["seo"] = {"title": seo_t or name, "description": seo_d}
    return pin, price, (barcodes[0] if barcodes else ""), images, status


def _find_by_sku(code):
    q = '''query($q:String!){ productVariants(first:1, query:$q){ nodes{ id
             product{ id } } } }'''
    d = s.gql(q, {"q": f"sku:{code}"})
    nodes = (d.get("productVariants") or {}).get("nodes") or []
    return (nodes[0]["product"]["id"], nodes[0]["id"]) if nodes else (None, None)


def sync_code(code, do_write=False):
    pin, price, barcode, images, status = build_input(code)
    prod_id, var_id = _find_by_sku(code)
    plan = {"code": code, "exists": bool(prod_id), "price": price, "barcode": barcode,
            "images": len(images), "status": status, "input": pin}
    # Barsy flags say this must NOT be on the online store (is_for_sale=0 / is_service / deleted):
    # archive it if it's already there, otherwise don't create it.
    if status is None:
        if not do_write:
            return {**plan, "action": ("archive" if prod_id else "skip"), "dry_run": True}
        if prod_id:
            s.gql('''mutation($p:ProductUpdateInput!){ productUpdate(product:$p){
                    product{ id } userErrors{ message } } }''', {"p": {"id": prod_id, "status": "ARCHIVED"}})
            return {**plan, "archived": True, "written": True}
        return {**plan, "skipped": "not sellable in Barsy (is_for_sale=0 / service / deleted)"}
    if not do_write:
        return plan
    # create or update the product core
    if prod_id:
        d = s.gql('''mutation($p:ProductUpdateInput!){ productUpdate(product:$p){
                    product{ id } userErrors{ field message } } }''',
                  {"p": {**{k: v for k, v in pin.items()}, "id": prod_id}})
        pid = d["productUpdate"]["product"]["id"]
    else:
        d = s.gql('''mutation($p:ProductCreateInput!){ productCreate(product:$p){
                    product{ id variants(first:1){ nodes{ id } } } userErrors{ field message } } }''',
                  {"p": pin})
        pid = d["productCreate"]["product"]["id"]
        var_id = d["productCreate"]["product"]["variants"]["nodes"][0]["id"]
    # variant: price + barcode + sku (+ parcel weight for courier quotes, when Barsy has it)
    inv = {"sku": str(code), "tracked": False}
    w = _barsy_weight_kg(code)                          # positive float kg, or None
    if w is not None:                                   # omit the field entirely when missing/≤0 (never 0)
        inv["measurement"] = {"weight": {"value": w, "unit": "KILOGRAMS"}}
    vin = {"id": var_id, "price": price or "0", "barcode": barcode, "inventoryItem": inv}
    s.gql('''mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){
             productVariantsBulkUpdate(productId:$pid, variants:$v){ userErrors{ field message } } }''',
          {"pid": pid, "v": [vin]})
    # images: publish our PROCESSED cutouts (image-pipeline/processed/<code>.png) by uploading the
    # bytes straight to Shopify via staged uploads — same processed images that go to Barsy, never
    # the raw supplier scrape. Only on CREATE (productCreateMedia APPENDS, so re-syncing an existing
    # product would duplicate media). (owner 2026-07-20)
    if not prod_id:
        import barsy_push as _bp
        media = []
        for p in _bp._processed_images(code, max_images=8):
            try:
                media.append({"originalSource": _staged_upload(p.name, p.read_bytes()),
                              "mediaContentType": "IMAGE"})
            except Exception as e:
                plan.setdefault("image_warn", str(e)[:120])
        if media:
            try:
                s.gql('''mutation($pid:ID!,$m:[CreateMediaInput!]!){
                         productCreateMedia(productId:$pid, media:$m){ mediaUserErrors{ field message } } }''',
                      {"pid": pid, "m": media})
            except s.ShopifyError as e:
                plan["image_warn"] = str(e)[:120]
    plan["product_id"] = pid
    _cf_upsert(code, pid, pin, price, images)
    return plan


def _cf_upsert(code, pid, pin, price, images):
    """Push to the Cloudflare semantic-search worker (migration requirement)."""
    import urllib.request
    e = s._env() if False else None
    import os
    url = None
    from pathlib import Path
    envp = Path.home() / ".hermes" / ".env"
    for ln in envp.read_text(encoding="utf-8").splitlines() if envp.exists() else []:
        if ln.startswith("CF_SEARCH_UPSERT="):
            url = ln.split("=", 1)[1].strip()
    if not url:
        return
    body = {"products": [{"id": str(code), "handle": str(code),
            "title": pin["title"], "text": pin["title"] + " " + " ".join(pin["tags"])
            + " " + re.sub("<[^>]+>", " ", pin["descriptionHtml"])[:300],
            "price": price, "image": images[0] if images else ""}]}
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
              headers={"Content-Type": "application/json", "User-Agent": "darling-pipeline/1.0"},
              method="POST")
        urllib.request.urlopen(req, timeout=20)
    except Exception:
        pass                                 # search upsert is best-effort


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "sync", "delete-test"])
    ap.add_argument("arg")
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    if a.action == "delete-test":
        if not a.confirm:
            print("need --confirm"); return
        print(s.gql('mutation($id:ID!){ productDelete(input:{id:$id}){ deletedProductId userErrors{ message } } }',
                    {"id": a.arg}))
        return
    out = sync_code(a.arg, do_write=(a.action == "sync" and a.confirm))
    print(json.dumps(out, ensure_ascii=False, indent=1)[:1600])


if __name__ == "__main__":
    main()
