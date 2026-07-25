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
# Darling is a HOME & GARDEN shop; pipeline-v3/leaf_forms.json (the name composer) covers HG 100%.
# Restrict the bundle to the "hg" vertical → full composer coverage + a fraction of the size.
VERTICAL = os.environ.get("TAXONOMY_VERTICAL", "hg")
os.makedirs(OUT, exist_ok=True)


def _keep(cid):
    return cid == VERTICAL or cid.startswith(VERTICAL + "-")


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

schemas = {}
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
            out.append({"name": g.get("група") or "", "handle": g.get("shopify_attr") or "",
                        "values": vals, "free": len(vals) == 0})
        if cid and out:
            schemas[cid] = out

json.dump(cats, open(os.path.join(OUT, "categories.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(schemas, open(os.path.join(OUT, "schemas.json"), "w", encoding="utf-8"), ensure_ascii=False)
# remove the old agent-pipeline-sourced files if present (superseded)
for old in ("cat_attrs.json", "attributes.json"):
    p = os.path.join(OUT, old)
    if os.path.exists(p):
        os.remove(p)
print(f"categories={len(cats)} schemas={len(schemas)}")
for fn in ("categories.json", "schemas.json"):
    print(fn, os.path.getsize(os.path.join(OUT, fn)) // 1024, "KB")
