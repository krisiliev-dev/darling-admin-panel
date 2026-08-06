# -*- coding: utf-8 -*-
"""Push enriched Darling products into Barsy (owner 2026-07-19).

Barsy already holds Darling's catalog (migrated from Microinvest): each article carries
the Microinvest `code` as its business key. So we NEVER blindly create — we resolve by
`code`:

  * code already in Barsy  -> Articles_update (enrich-in-place: add barcode, brand +
    attributes as `details`, public name; PRICE/QTY left untouched — those are owned by
    Barsy/Microinvest per the price policy).
  * code not in Barsy      -> Articles_create (full payload from barsy_collect).

Source of enrichment = our SQLite system-of-record (db.get_record), preferring the owner-
approved/edited fields when an approval exists. Barcodes = the ingested GS1 barcodes.

SAFETY: dry-run by default — prints the exact JSON body per product and writes NOTHING.
A real write requires BOTH `--push` and `--confirm`. Scope with `--code C` / `--limit N`.

  python barsy_push.py plan --code 210038        # show the exact update body, no write
  python barsy_push.py plan --limit 20           # dry-run the next 20 enrich candidates
  python barsy_push.py push --code 210038 --push --confirm   # ONE real update
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import barsy_api as b

HERE = Path(__file__).parent

# our normalized unit (Мярка / amount_type_name_short) -> Barsy amount_type_id
UNIT_TO_ID = {
    "бр": 1, "бр.": 1, "брой": 1, "": 1,
    "л": 2, "литър": 2, "кг": 3, "килограм": 3,
    "ч": 4, "д": 5, "км": 6, "м": 7, "метър": 7,
    "мин": 8, "м2": 11, "кв.м": 11, "м3": 16,
}
LANG = "bg_BG"


def _catalog():
    """Live {code: article} map of everything currently in Barsy (by business code).

    Fetched under the ONLINE delivery channel ("address") with current_price requested, so each
    article carries BOTH `actual_price` (base) and `current_price` (the price under the online
    channel, reflecting any active Barsy Pricelists rule scoped to online). The filter annotates
    pricing context only — it returns the identical article set as an unfiltered fetch (verified).
    Consumers that publish to the storefront use current_price (see connector `_price`); with no
    online rule the two are equal, so this is a no-op until the owner sets channel pricing."""
    arts = b.call("Articles_getlist", {"filters": {"delivery_type_id": "address"},
                                        "extra_properties": {"current_price": 1, "extra_data": 1}})
    return {str(a["code"]): a for a in arts if a.get("code")}


def _barcodes():
    import ean_brand as eb
    m = defaultdict(list)
    for d in eb._load(eb.BARCODES):
        if d.get("cls") == "gs1":
            m[str(d["code"])].append(str(d["ean"]))
    return m


def _fields(code):
    """Enriched fields for a code — owner-approved/edited version wins over DB canonical."""
    import db
    rec = db.get_record(code) or {}
    f = dict(rec.get("fields") or {})
    meta = rec.get("meta") or {}
    # approvals overlay
    try:
        import barsy_collect as bcol
        appr = bcol._approved().get(code)
        if appr:
            f.update({k: v for k, v in appr.items() if v not in (None, "")})
    except Exception:
        pass
    return f, meta


def _details(fields):
    """Barsy article `details` (characteristics) from our attributes + brand.
    Attributes are stored as 'Key: Value; Key: Value'. Brand becomes a 'Марка' detail."""
    rows = []
    brand = str(fields.get("Бранд") or "").strip()
    if brand:
        rows.append({"detail_name": "Марка", "detail_value": brand})
    for part in str(fields.get("Атрибути") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k, v = k.strip(), re.sub(r"\s+", " ", v.strip())
            if k and v:
                rows.append({"detail_name": k, "detail_value": v})
    return rows


def _unit_id(fields, existing):
    u = str(fields.get("Мярка") or "").strip().lower().rstrip(".")
    if u in UNIT_TO_ID:
        return UNIT_TO_ID[u]
    return existing.get("amount_type_id") or 1        # keep Barsy's, else piece


_CATMAP = None


def _cat_id_for(meta, ensure=False):
    """Barsy cat_id for a product's Shopify leaf gid. Read-only against the build checkpoint by
    default; with ensure=True (the write path) it CREATES the leaf + ancestors just-in-time so a
    product never waits on the full background taxonomy build."""
    gid = (meta or {}).get("leaf_gid")
    if not gid:
        return None
    if ensure:
        import barsy_taxonomy as bt
        try:
            return bt.ensure_path(gid)
        except Exception:
            pass                                # fall back to read-only lookup below
    global _CATMAP
    if _CATMAP is None:
        import json
        p = HERE / "barsy_cat_map.json"
        _CATMAP = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _CATMAP.get(gid)


def _num(v):
    """Parse a Microinvest numeric string ('2', '2,5', '') -> float or None."""
    s = str(v or "").strip().replace(",", ".")
    try:
        return float(s) if s and s.lower() != "nan" else None
    except ValueError:
        return None


def build(code, catalog, bc, ensure_cat=False):
    """Return (mode, body) for one code. mode is 'update' or 'create'. body is the API arg.
    Only GAP fields are set on update; price/qty are never written here."""
    fields, meta = _fields(code)
    existing = catalog.get(code)
    barcodes = bc.get(code) or []
    art = {}
    name = str(fields.get("Наименование") or "").strip()
    if name:
        art["article_name"] = name
        art["article_name_ml"] = {LANG: name}          # Barsy stores ml as a lang-keyed map
    if barcodes:
        # Barsy Articles_create/_update want a SINGLE barcode STRING, not an array — sending a
        # list 500s the API ("непредвидена грешка"). Use the primary GS1 barcode. (owner 2026-07-20)
        art["barcode"] = barcodes[0]
    details = _details(fields)
    if details:
        art["details"] = details
    desc = str(fields.get("Описание") or "").strip()
    cat_id = _cat_id_for(meta, ensure=ensure_cat)       # Shopify leaf gid -> Barsy cat_id
    if cat_id:
        art["cat_id"] = cat_id
        art["master_cat_id"] = cat_id
    aliases = [s.strip() for s in str(fields.get("Синоними") or "").split(",") if s.strip()]
    if aliases:
        art["article_aliases"] = aliases

    # Mira-N products are made-to-order (sourced from the supplier on demand) → sellable at 0 stock.
    # Store the policy IN Barsy via sale_below_zero (YES/NO/DEF) so Barsy is the single source of
    # truth; the Shopify connector reads it to set the inventory policy. (owner 2026-07-20)
    if "мира" in str(fields.get("Доставчик") or "").lower() \
            or "mira-n.net" in str(fields.get("Снимки") or "").lower():
        art["sale_below_zero"] = "YES"

    # Meta (SEO) title/description + weight + variant grouping have NO native Barsy field, so they
    # live in the free-form `extra_data` OBJECT (owner 2026-07-20; read back via
    # extra_properties:{extra_data:1}). Keeps Barsy the single source of truth for them.
    extra = {}
    _mt = str(fields.get("Мета наименование") or "").strip()
    _md = str(fields.get("Мета описание") or "").strip()
    _wt = str(fields.get("Тегло (кг)") or "").strip()
    _sv = str(fields.get("Свързани") or "").strip()
    if _mt:
        extra["seo_title"] = _mt
    if _md:
        extra["seo_description"] = _md
    if _wt:
        extra["weight_kg"] = _wt
    if _sv:                                            # variant/connected product codes (grouping)
        extra["variant_codes"] = [p.split("|", 1)[0].strip() for p in _sv.split(";") if p.strip()]
    if extra:
        art["extra_data"] = extra

    try:
        import barsy_collect as bcol
        pq = bcol._export_price_qty().get(code) or {}
    except Exception:
        pq = {}
    min_amt = _num(pq.get("min_qty"))                   # Microinvest reorder threshold

    if existing:
        aid = existing["article_id"]
        art["amount_type_id"] = _unit_id(fields, existing)
        if min_amt:
            art["min_amount"] = min_amt
        # enrich-in-place: only fill Barsy gaps, never clobber a populated field
        if existing.get("barcode"):
            art.pop("barcode", None)
        # aliases/synonyms are attached once at create; re-sending an already-attached synonym
        # 501s ("вече е прикачен към артикул") and would loop forever on every run. Drop on update.
        art.pop("article_aliases", None)
        if not existing.get("description") and desc:
            art["description_ml"] = {LANG: desc}
        # Articles_update wants article_id at TOP level (not inside `article`)
        return "update", {"article_id": aid, "article": art}
    # create (approved products not yet in Barsy)
    art["amount_type_id"] = _unit_id(fields, {})
    art["ref_num"] = str(fields.get("Каталожен номер") or "") or None
    art["code"] = code
    # Created OFFLINE. Owner's rule (2026-08-06): nothing reaches the storefront without at
    # least one picture - and at create time there cannot be one yet, because the images are
    # uploaded against an article_id that does not exist until this call returns.
    # publish_if_imaged() flips this to 1 once a file is actually ON the article; a product
    # whose cutout never landed stays a draft instead of going online blank.
    # is_for_sale stays 1: sellable in the shop and over the phone is a different question
    # from listed on the website.
    art["is_public"] = 0
    art["is_for_sale"] = 1
    if min_amt:
        art["min_amount"] = min_amt
    if desc:
        art["description_ml"] = {LANG: desc}
    if pq.get("price"):
        art["actual_price"] = pq["price"]
    return "create", {"article": art}


def push_one(code, catalog=None, bc=None, images=True):
    """Create/update ONE product in Barsy (+ its images). Returns (mode, article_id).
    Used by the approved→store automation. Idempotent (update if the code already exists)."""
    if catalog is None:
        catalog = _catalog()
    if bc is None:
        bc = _barcodes()
    mode, body = build(code, catalog, bc, ensure_cat=True)
    method = "Articles_update" if mode == "update" else "Articles_create"
    out = b.call(method, body)
    aid = body.get("article_id") or (out if isinstance(out, int)
                                     else (out.get("article_id") if isinstance(out, dict) else None))
    if aid and mode == "create":
        # A created article is offline until a picture is on it (see build()). An UPDATE is
        # left alone on purpose: an existing product's online state belongs to the owner and
        # to the panel, and a pipeline run must not quietly take listings down.
        if images:
            published, reason = publish_if_imaged(code, aid)
        else:
            published, reason = False, "images_skipped"
        if not published:
            print(f"   -> code={code} stays a DRAFT in Barsy: {reason}")
    elif images and aid:
        push_images(code, aid)
    return mode, aid


PROCESSED_DIR = Path(__file__).parent.parent / "image-pipeline" / "processed"


def _processed_images(code, max_images=6):
    """The PROCESSED cutouts for a code: image-pipeline/processed/<code>.png (+ <code>_N.png
    for extra views). This is the ONLY image source we publish."""
    return (sorted(PROCESSED_DIR.glob(f"{code}.png")) +
            sorted(PROCESSED_DIR.glob(f"{code}_*.png")))[:max_images]


def push_images(code, article_id, max_images=6):
    """Upload the product's PROCESSED images to the Barsy article as PICTURE files (Barsy
    resizes + sets article.picture). Returns (uploaded, err).

    Owner 2026-07-20: publish ONLY our processed cutouts (image-pipeline/processed/<code>*.png),
    NEVER the raw supplier scrape. If no processed image exists we upload nothing (return a
    marker) rather than fall back to the supplier URL — a product without a processed image is
    not ready to show. QC-failed cutouts don't reach here: they're flagged needs_human upstream,
    so they never get approved/pushed.
    Idempotent-ish: skips filenames already present on the article."""
    files = _processed_images(code, max_images)
    if not files:
        return 0, "no_processed_image"
    try:
        have = {f.get("name") for f in b.call("Files_getlist",
                {"obj_type": "articles", "obj_id": str(article_id)})}
    except Exception:
        have = set()
    up = 0
    err = None
    for p in files:
        fn = p.name
        if fn in have:
            continue
        try:
            b.upload_image(article_id, p.read_bytes(), fn)
            up += 1
        except Exception as e:
            err = f"{fn}: {str(e)[:80]}"
    return up, err


def _article_files(article_id):
    """What Barsy currently holds against an article, or None if we could not ask.

    THREE states, never a boolean: [] means Barsy answered and there is nothing on the
    article; None means the question got no answer. Those lead to different actions, and
    folding them together publishes a blank product on a timeout.
    """
    try:
        files = b.call("Files_getlist", {"obj_type": "articles", "obj_id": str(article_id)})
    except Exception:
        return None
    return files if isinstance(files, list) else None


def publish_if_imaged(code, article_id):
    """Upload the processed cutouts, then put the article online ONLY if one is really on it.

    Returns (published, reason):
        (True,  "")                     a picture is on the article and is_public is now 1
        (False, "no_processed_image")   there was nothing to upload
        (False, "<file>: <error>")      the upload itself failed
        (False, "picture_check_failed") Barsy did not answer when asked what is on the article

    The decision reads what Barsy HOLDS, not what we just sent. push_images skips filenames
    already present, so a correct second run uploads zero - and "uploaded 0" and "has no
    picture" are different facts. Deciding on the upload counter would leave every
    already-imaged product a permanent draft on every rerun.
    """
    _up, err = push_images(code, article_id)
    files = _article_files(article_id)
    if files is None:
        return False, "picture_check_failed" + (f" ({err})" if err else "")
    if not files:
        return False, err or "no_processed_image"
    # Articles_update takes the record id at TOP level and merges the partial `article` body -
    # the enrich-in-place path in build() relies on exactly that behaviour.
    b.call("Articles_update", {"article_id": article_id, "article": {"is_public": 1}})
    return True, ""


def delete_test_products(keep_ids=(16,), do_write=False):
    """Remove the earlier pilot/test articles (owner-authorised cleanup 2026-07-19).
    HARD delete (Articles_delete removes the row) — a full backup is taken first so any
    of these can be re-created from it. Deletes children (parent_id set) before parents.
    Keeps `keep_ids` (Barsy system defaults, e.g. 16 'Авансово плащане')."""
    import json as _json
    import pathlib
    arts = b.call("Articles_getlist", {"filters": {}})
    victims = [a for a in arts if a["article_id"] not in keep_ids]
    # backup EVERYTHING we're about to delete (full record + details)
    bak = {"ts": "2026-07-19", "articles": []}
    for a in victims:
        rec = dict(a)
        try:
            rec["_details"] = b.call("Articledetails_get", {"article_id": a["article_id"]})
        except Exception:
            rec["_details"] = None
        bak["articles"].append(rec)
    p = pathlib.Path("barsy_delete_backup.json")
    p.write_text(_json.dumps(bak, ensure_ascii=False, indent=1), encoding="utf-8")
    # children first (those with a parent_id), then parents
    order = sorted(victims, key=lambda a: 0 if a.get("parent_id") else 1)
    print(f"backup: {p} ({len(victims)} articles). keeping ids {list(keep_ids)}.")
    if not do_write:
        print("DRY-RUN — pass --push --confirm to actually delete.")
        for a in order[:5]:
            print(f"  would delete {a['article_id']} code={a.get('code')} {a['article_name'][:40]}")
        print(f"  ... {len(order)} total")
        return
    ok = fail = 0
    for a in order:
        try:
            b.call("Articles_delete", {"article_id": a["article_id"]})
            ok += 1
        except b.BarsyError as e:
            fail += 1
            print(f"  FAIL {a['article_id']} ({a.get('code')}): {str(e)[:120]}")
    left = b.call("Articles_getlist", {"filters": {}})
    print(f"deleted {ok}, failed {fail}. remaining articles: {len(left)} "
          f"({[x['article_name'] for x in left][:5]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "push", "delete-test"])
    ap.add_argument("--code", action="append", help="restrict to these codes")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--push", action="store_true", help="actually call the API (writes)")
    ap.add_argument("--confirm", action="store_true", help="required with --push")
    ap.add_argument("--images", action="store_true", help="also upload product images")
    a = ap.parse_args()

    if a.action == "delete-test":
        delete_test_products(do_write=(a.push and a.confirm))
        return

    catalog = _catalog()
    bc = _barcodes()
    if a.code:
        codes = a.code
    else:
        # default candidate set: in-Barsy products we can enrich (have barcode or attrs)
        codes = [c for c in catalog if bc.get(c) or _fields(c)[0].get("Атрибути")][:a.limit]

    writing = a.action == "push" and a.push and a.confirm
    if a.action == "push" and not writing:
        print("REFUSING to write: `push` needs both --push and --confirm. Showing dry-run.\n")

    for code in codes:
        mode, body = build(code, catalog, bc)
        art = body["article"]
        gaps = [k for k in ("barcode", "details", "article_name_ml", "description_ml")
                if k in art]
        print(f"[{mode}] code={code} article_id={body.get('article_id','-')} "
              f"setting={gaps}")
        if a.action == "plan":
            print(json.dumps(body, ensure_ascii=False, indent=1)[:1400])
            print("-" * 60)
            continue
        if not writing:
            continue
        method = "Articles_update" if mode == "update" else "Articles_create"
        try:
            out = b.call(method, body)
            print(f"   -> {method} OK: {json.dumps(out, ensure_ascii=False)[:300]}")
            aid = body.get("article_id") or (out if isinstance(out, int)
                                             else (out.get("article_id") if isinstance(out, dict) else None))
            if aid and mode == "create":
                if a.images:
                    published, reason = publish_if_imaged(code, aid)
                    print("   -> " + ("ONLINE (picture confirmed on the article)" if published
                                      else f"stays a DRAFT: {reason}"))
                else:
                    print("   -> created as a DRAFT: no --images, so no picture could land "
                          "and it will not appear on the site")
            elif a.images and aid:
                up, err = push_images(code, aid)
                print(f"   -> images: uploaded {up}" + (f", err {err}" if err else ""))
        except b.BarsyError as e:
            print(f"   -> {method} FAIL: {e}")


if __name__ == "__main__":
    main()
