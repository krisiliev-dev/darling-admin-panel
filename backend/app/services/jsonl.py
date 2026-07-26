# -*- coding: utf-8 -*-
"""Tolerant JSONL reader — a leaf utility shared by the data-file loaders (supplier catalogue,
supplier-code index, barcode index)."""
import json


def iter_jsonl(path: str):
    """Yield parsed objects from a .jsonl file, SKIPPING blank/malformed lines individually. A single
    bad line must not truncate the rest of the file (these maps have 20k+ rows)."""
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
