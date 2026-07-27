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
    pin = {
        "title": name,
        "descriptionHtml": str(fields.get("Описание") or ""),
        "vendor": str(fields.get("Бранд") or "").strip() or "Darling",
        "productType": meta.get("leaf_name") or "",
        "status": "DRAFT",
        "tags": _tags(fields, meta.get("leaf_full") or ""),
    }
    if leaf_gid:
        pin["category"] = leaf_gid          # Shopify taxonomy gid == our leaf_gid
    seo_t = str(fields.get("Мета наименование") or "").strip()
    seo_d = str(fields.get("Мета описание") or "").strip()
    if seo_t or seo_d:
        pin["seo"] = {"title": seo_t or name, "description": seo_d}
    return pin, price, (barcodes[0] if barcodes else ""), images


def _find_by_sku(code):
    q = '''query($q:String!){ productVariants(first:1, query:$q){ nodes{ id
             product{ id } } } }'''
    d = s.gql(q, {"q": f"sku:{code}"})
    nodes = (d.get("productVariants") or {}).get("nodes") or []
    return (nodes[0]["product"]["id"], nodes[0]["id"]) if nodes else (None, None)


def sync_code(code, do_write=False):
    pin, price, barcode, images = build_input(code)
    prod_id, var_id = _find_by_sku(code)
    plan = {"code": code, "exists": bool(prod_id), "price": price, "barcode": barcode,
            "images": len(images), "input": pin}
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
    # variant: price + barcode + sku
    vin = {"id": var_id, "price": price or "0", "barcode": barcode,
           "inventoryItem": {"sku": str(code), "tracked": False}}
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
