# -*- coding: utf-8 -*-
"""Precompute SLIM Shopify-taxonomy indexes for the panel, sourced from PIPELINE-V3 so leaf names
align with pipeline-v3/leaf_forms.json (the name composer). Bundle output into the panel repo.

Sources (pipeline-v3):
  taxonomy_slim.json   [{gid, full(EN), bg(BG LEAF name), leaf}]
  leaf_schemas.jsonl   {gid, schema:[{група(BG attr), стойности:[BG values], shopify_attr(handle)}]}

Outputs (OUT):
  categories.json  [{id, en, bg, leaf}]              — id = short gid; en/bg = breadcrumbs
  schemas.json     {id: [{name, handle, values:[bg], free}]}  — per-leaf attribute schema (BG)

Usage: python precompute_taxonomy.py <pipeline-v3 dir> <out dir>
"""
import json, os, re, sys

SRC = sys.argv[1]   # pipeline-v3
OUT = sys.argv[2]   # backend/app/data/shopify_taxonomy
# The FULL taxonomy is bundled (some products fall outside Home & Garden). Optionally restrict to one
# vertical via TAXONOMY_VERTICAL (e.g. "hg"); empty/unset = keep everything. NB: pipeline-v3/
# leaf_forms.json (singular+gender for the name composer) covers Home & Garden fully; leaves in other
# verticals fall back to the leaf label (no gender agreement) until leaf_forms is extended.
VERTICAL = os.environ.get("TAXONOMY_VERTICAL", "").strip()
os.makedirs(OUT, exist_ok=True)


def _keep(cid):
    return (not VERTICAL) or cid == VERTICAL or cid.startswith(VERTICAL + "-")


def short(gid):
    return gid.split("/")[-1]


slim = json.load(open(os.path.join(SRC, "taxonomy_slim.json"), encoding="utf-8"))
bg_leaf = {short(c["gid"]): c.get("bg") or "" for c in slim}   # id -> BG leaf name


def bg_breadcrumb(cid):
    chain, node = [], cid
    while node:
        if bg_leaf.get(node):
            chain.append(bg_leaf[node])
        parent = re.sub(r"-\d+$", "", node)
        node = parent if parent != node else ""
    return " > ".join(reversed(chain))


cats = [{"id": short(c["gid"]), "en": c.get("full") or "", "bg": bg_breadcrumb(short(c["gid"])),
         "leaf": bool(c.get("leaf"))} for c in slim if _keep(short(c["gid"]))]

# value-lists repeat massively across leaves (the same Color/Material list on thousands of leaves) —
# intern them into a shared pool so schemas.json stays small. Format:
#   {"pool": [[values...], ...], "leaves": {id: [[name, handle, pool_idx, free], ...]}}
pool_idx: dict = {}
pool_list: list = []


def _ref(vals):
    key = tuple(vals)
    if key not in pool_idx:
        pool_idx[key] = len(pool_list)
        pool_list.append(vals)
    return pool_idx[key]


leaves = {}
with open(os.path.join(SRC, "leaf_schemas.jsonl"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = short(row.get("gid", ""))
        if not _keep(cid):
            continue
        out = []
        for g in row.get("schema", []):
            vals = [str(v) for v in (g.get("стойности") or []) if str(v).strip()]
            out.append([g.get("група") or "", g.get("shopify_attr") or "", _ref(vals), len(vals) == 0])
        if cid and out:
            leaves[cid] = out

json.dump(cats, open(os.path.join(OUT, "categories.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"pool": pool_list, "leaves": leaves},
          open(os.path.join(OUT, "schemas.json"), "w", encoding="utf-8"), ensure_ascii=False)
# remove the old agent-pipeline-sourced files if present (superseded)
for old in ("cat_attrs.json", "attributes.json"):
    p = os.path.join(OUT, old)
    if os.path.exists(p):
        os.remove(p)
print(f"categories={len(cats)} leaves={len(leaves)} value_lists={len(pool_list)}")
for fn in ("categories.json", "schemas.json"):
    print(fn, os.path.getsize(os.path.join(OUT, fn)) // 1024, "KB")
