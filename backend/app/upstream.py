# -*- coding: utf-8 -*-
"""One place to translate a failing external-system call (Barsy / courier / Shopify) into a
clean 502, instead of ad-hoc try/except with inconsistent truncation scattered across routers."""
from contextlib import contextmanager

from fastapi import HTTPException, status


@contextmanager
def upstream(label: str):
    """`with upstream("Barsy наличности"): out = barsy.call(...)` — re-raises HTTPExceptions
    unchanged, wraps anything else as a 502 with a uniform, bounded message."""
    try:
        yield
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — deliberately broad: any upstream failure → 502
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{label}: {str(e)[:180]}")
