# -*- coding: utf-8 -*-
"""Precompute SLIM Shopify-taxonomy indexes to bundle INTO the panel repo (so it's always deployed,
not dependent on the sibling agent-pipeline being present on the server).

Outputs (to OUT):
  categories.json  : [{id, en, bg, leaf}]                       (EN + BG breadcrumb, per category)
  cat_attrs.json   : {category_id: [attribute_handle, ...]}     (which attributes each category has)
  attributes.json  : {handle: {en, bg, free, values:[{h,en,bg}]}}  (attr defs + allowed values, EN+BG)
"""
import json, re, os, sys

SRC = sys.argv[1]   # agent-pipeline/shopify_taxonomy
OUT = sys.argv[2]   # backend/app/data/shopify_taxonomy
os.makedirs(OUT, exist_ok=True)


def parse_bg(path):
    """{handle_underscored -> bg name} from a *_bg.yml (tiny line reader, no pyyaml)."""
    out, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^    ([^\s:][^:]*):\s*$", line)
            if m:
                cur = m.group(1).strip()
                continue
            n = re.match(r"^      name:\s*(.+?)\s*$", line)
            if n and cur:
                out[cur] = n.group(1)
                cur = None
    return out


def u(h):
    return h.replace("-", "_")


cats_bg_by_id = parse_bg(os.path.join(SRC, "categories_bg.yml"))     # id -> bg leaf name
attrs_bg = parse_bg(os.path.join(SRC, "attributes_bg.yml"))          # attr handle -> bg
values_bg = parse_bg(os.path.join(SRC, "values_bg.yml"))            # value handle -> bg


def bg_breadcrumb(cid):
    chain, node = [], cid
    while node:
        if node in cats_bg_by_id:
            chain.append(cats_bg_by_id[node])
        parent = re.sub(r"-\d+$", "", node)
        node = parent if parent != node else ""
    return " > ".join(reversed(chain))


# --- attributes: handle -> {en, bg, free, values} -------------------------------------
aj = json.load(open(os.path.join(SRC, "attributes.json"), encoding="utf-8"))
attrs = {}
for a in aj["attributes"]:
    h = a["handle"]
    vals = [{"h": v["handle"], "en": v["name"], "bg": values_bg.get(u(v["handle"]), v["name"])}
            for v in (a.get("values") or [])]
    # "free" = free-text (no curated value list, or only an 'other'); otherwise a picklist
    real = [v for v in vals if not v["h"].endswith("__other")]
    attrs[h] = {"en": a["name"], "bg": attrs_bg.get(u(h), a["name"]),
                "free": len(real) == 0, "values": vals}

# --- categories + category -> attribute handles ---------------------------------------
cj = json.load(open(os.path.join(SRC, "categories.json"), encoding="utf-8"))
cats, cat_attrs = [], {}
# leaf = has no child (no other category lists it as parent)
has_child = set()
flat = []
for vert in cj["verticals"]:
    for c in vert["categories"]:
        flat.append(c)
        if c.get("parent_id"):
            has_child.add(c["parent_id"])
for c in flat:
    sid = c["id"].split("/")[-1]
    cats.append({"id": sid, "en": c.get("full_name", ""), "bg": bg_breadcrumb(sid),
                 "leaf": c["id"] not in has_child})
    cat_attrs[sid] = [a["handle"] for a in (c.get("attributes") or [])]

json.dump(cats, open(os.path.join(OUT, "categories.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(cat_attrs, open(os.path.join(OUT, "cat_attrs.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(attrs, open(os.path.join(OUT, "attributes.json"), "w", encoding="utf-8"), ensure_ascii=False)
print(f"categories={len(cats)} cat_attrs={len(cat_attrs)} attributes={len(attrs)}")
for f in ("categories.json", "cat_attrs.json", "attributes.json"):
    print(f, os.path.getsize(os.path.join(OUT, f)) // 1024, "KB")
