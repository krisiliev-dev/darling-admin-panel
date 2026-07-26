# -*- coding: utf-8 -*-
"""Text normalisation shared by fuzzy matching (delivery header/name matching, supplier search)."""
import re
import unicodedata


def norm(s: str) -> str:
    """Lowercase, strip accents/spaces/punct — for fuzzy header + name comparison."""
    s = unicodedata.normalize("NFKD", str(s or "")).lower()
    return re.sub(r"[\s\.\-_/№#]+", "", s)
