# -*- coding: utf-8 -*-
"""One place to parse the id out of a Barsy create/update response.

Barsy is inconsistent: a create may return a bare int, a digit string, or a dict carrying the id
under `article_id` / `storeload_id` / `id`. This was hand-rolled in three subtly-different helpers
(intake, intake_delivery, and the vendored barsy_push) — consolidated here so they can't drift.
"""


def _as_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def extract_id(out, keys=("article_id", "id")) -> int | None:
    """The int id from a Barsy response, or None. `keys` is the dict-key search order (pass
    ("article_id", "storeload_id", "id") when the response might be a storeload)."""
    direct = _as_int(out)
    if direct is not None:
        return direct
    if isinstance(out, dict):
        for k in keys:
            iv = _as_int(out.get(k))
            if iv is not None:
                return iv
    return None
