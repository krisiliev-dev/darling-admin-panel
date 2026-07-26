# -*- coding: utf-8 -*-
"""v3 deterministic product-name composer, VENDORED from pipeline-v3/name_builder.py so the panel is
self-contained (data bundled in app/data/naming/ — keep in sync if the pipeline's rules change).

NO LLM at runtime. Name = prefix + gender-agreed adjectives (Color→Material→Shape) + the Shopify
LEAF's singular BG noun + suffix phrases + size. Gender comes from leaf_forms.json; adjective forms
from name_rules.json. An adjective with no known gender form is DROPPED (empty > wrong grammar).
NB: v3 names carry NO brand (brand is a separate product field, not part of the title)."""
import json
import os
import re

_DIR = os.path.join(os.path.dirname(__file__), "data", "naming")
_RULES = None
_FORMS = None


class _Blank(dict):
    def __missing__(self, k):
        return ""


def _load():
    global _RULES, _FORMS
    if _RULES is None:
        with open(os.path.join(_DIR, "name_rules.json"), encoding="utf-8") as f:
            _RULES = json.load(f)
        with open(os.path.join(_DIR, "leaf_forms.json"), encoding="utf-8") as f:
            ff = json.load(f)
        _FORMS = ff.get("forms", ff)
    return _RULES, _FORMS


def _adj_index(rules):
    idx = {}
    for key, forms in rules["adjectives"].items():
        idx[key.lower()] = forms
        for a in forms.get("aliases", []):
            idx[a.lower()] = forms
    return idx


def _get_attr(attrs, names):
    low = {str(k).strip().lower(): v for k, v in (attrs or {}).items()}
    for n in names:
        v = low.get(str(n).strip().lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


_UNITS = [("mm", "мм"), ("cm", "см"), ("ml", "мл"), ("cl", "сл"), ("kg", "кг"),
          ("mg", "мг"), ("m", "м"), ("l", "л"), ("g", "г")]


def _glue(s):
    s = re.sub(r"\s+", "", str(s).strip()).replace(".", ",").replace("x", "х").replace("X", "х").replace("×", "х").lower()
    for lat, cyr in _UNITS:
        s = re.sub(lat + r"(?![a-z])", cyr, s)
    return s


def _size_str(facts):
    parts, seen = [], set()
    for keys in (("вместимост", "capacity", "обем", "volume"),
                 ("размери", "dimensions", "размер", "size")):
        for k in keys:
            v = (facts or {}).get(k)
            if v and str(v).strip():
                g = _glue(v)
                if g and g not in seen:
                    parts.append(g)
                    seen.add(g)
                break
    return " ".join(parts)


def _match_override(ov, attrs):
    if ov.get("enabled") is False:
        return False
    for k, want in (ov.get("when") or {}).items():
        have = _get_attr(attrs, [k])
        wants = want if isinstance(want, list) else [want]
        if not have or have.strip().lower() not in {str(w).strip().lower() for w in wants}:
            return False
    return True


def build(facts, style=None):
    """facts: {leaf_bg, attributes:{Material,Color,Shape,Feature}, + size fields
    (вместимост/размери/…)}. Returns the composed Bulgarian name (or '' if nothing to render)."""
    rules, forms = _load()
    idx = _adj_index(rules)
    attrs = facts.get("attributes") or {}

    leaf = (facts.get("leaf_bg") or "").split(" > ")[-1].strip()
    tf = forms.get(leaf) or {}
    type_noun = tf.get("singular") or leaf.lower()
    gender = tf.get("gender") or "m"

    prefix, drop, ov_style = "", set(), {}
    for ov in rules.get("overrides", []):
        if _match_override(ov, attrs):
            if ov.get("prefix"):
                prefix = ov["prefix"]
            drop |= {str(d) for d in ov.get("drop", [])}
            ov_style.update(ov.get("style") or {})

    chosen_style = {**(rules.get("style") or {}), **ov_style, **(style or {})}

    frags = {"pre": [], "post": []}
    for attr_name, cfg in rules["attributes"].items():
        if attr_name in drop:
            continue
        val = _get_attr(attrs, cfg.get("from", [attr_name]))
        if not val:
            continue
        variant_name = chosen_style.get(attr_name) or cfg.get("default")
        variant = (cfg.get("variants") or {}).get(variant_name)
        if not variant:
            continue
        if cfg.get("kind") == "adj":
            forms_a = idx.get(val.strip().lower())
            if not forms_a:
                continue
            data = _Blank(forms_a, adj=forms_a.get(gender) or forms_a.get("m"))
        else:
            data = _Blank(value=val)
        text = variant["tpl"].format_map(data).strip()
        if text:
            frags[variant.get("zone", "post")].append((attr_name, text))

    def _order(zone, key):
        order = rules.get(key, [])
        return sorted(frags[zone], key=lambda t: order.index(t[0]) if t[0] in order else 999)

    pieces = [prefix]
    pieces += [t for _, t in _order("pre", "pre_order")]
    pieces.append(type_noun)
    pieces += [t for _, t in _order("post", "post_order")]
    pieces.append(_size_str(facts))

    descriptor = re.sub(r"\s+", " ", " ".join(p for p in pieces if p)).strip()
    descriptor = descriptor[:1].upper() + descriptor[1:] if descriptor else descriptor
    # brand (v3): prepended VERBATIM (proper noun — not inflected, not re-cased) and OUTSIDE the
    # sentence-casing, so the descriptor's first word still capitalizes: "Luminarc Бяла керамична…".
    bcfg = rules.get("brand") or {"from": ["brand", "Brand", "Бранд"]}
    brand = (facts.get("brand") or _get_attr(attrs, bcfg.get("from", ["Brand"])) or "").strip()
    if brand and "Brand" not in drop:
        return f"{brand} {descriptor}".strip()
    return descriptor
