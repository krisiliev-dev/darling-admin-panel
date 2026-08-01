# -*- coding: utf-8 -*-
"""Why do DURALEX cards lack images? For every photo-less card in the DURALEX review row:
is the code in image_index (local file)? in tasks gt photo? in draft photos? does
products.jsonl carry an image_url or just a listing-page URL?"""
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("REVIEW_IMAGES_DIR", r"C:\Users\lora_\darling\image-work\originals")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.users import current_active_user  # noqa: E402


class FakeUser:
    email = "dev@darling.bg"; role = "owner"; is_active = True


app.dependency_overrides[current_active_user] = lambda: FakeUser()
c = TestClient(app)

q = c.get("/api/brand-registry/review?limit=30").json()["queue"]
row = next((r for r in q if "duralex" in (r.get("canonical") or "").lower()
            or "duralex" in (r.get("brand_id") or "").lower()), None)
if row is None:
    print("rows:", [(r["canonical"], r["prefix"], r.get("matched_count")) for r in q[:8]])
    sys.exit("no DURALEX row in top 30")
print(f"DURALEX row: prefix {row['prefix']} matched={row.get('matched_count')} "
      f"catalog={row['product_count']}")

d = c.get(f"/api/brand-registry/prefix/{row['prefix']}/products?limit=500").json()
noph = [i for i in d["items"] if not i.get("photo")]
print(f"drawer: {d['returned']} cards, {len(noph)} without photo")

idx = json.loads(Path(r"C:\Users\lora_\darling\admin-panel\backend\app\data\brands"
                      r"\image_index.json").read_text(encoding="utf-8"))

def intkey(s):
    s = str(s).strip()
    return str(int(s)) if s.isdigit() else s

pj = {}
for line in open(r"C:\Users\lora_\darling\pipeline-v3\products.jsonl", encoding="utf-8"):
    p = json.loads(line)
    pj[intkey(p["code"])] = p

print("\ncode | in_index | image_url? | supplier url (page)")
reasons = {}
for i in noph[:30]:
    ik = intkey(i["code"])
    p = pj.get(ik) or {}
    s = p.get("supplier") or {}
    raw = s.get("raw") or {}
    iu = str(raw.get("image_url") or "").strip()
    r = ("in_index_but_no_file" if ik in idx
         else "has_image_url_NOT_downloaded" if iu.startswith("http")
         else "listing_page_only_no_image_url")
    reasons[r] = reasons.get(r, 0) + 1
    print(f"  {i['code']} | {ik in idx} | {iu[:40] or '-'} | {str(s.get('url'))[:55]}")
print("\nreason tally:", reasons)
