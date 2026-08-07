# -*- coding: utf-8 -*-
"""Public (token-authed) category-visibility feed for the storefront.

Answers one question: which categories may the public see? The storefront turns the answer into a
query-level gate — a product is online only if its till flag AND its online flag are true AND it
sits in at least one enabled category, and "disabled" means not even searchable, so the gate has
to be a WHERE clause rather than a display-time filter.

Read-only. No panel-user auth; the caller presents the same shared secret as the courier office
lookup (X-Office-Token). Empty token in config => DISABLED (403), so it stays off until the owner
sets the secret.
"""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_async_session
from ..routers.catalog import build_enabled_feed

router = APIRouter(prefix="/api/public/categories", tags=["public-categories"])


def _check_token(token: str | None) -> None:
    """Constant-time check of the shared secret. Disabled (403) when no token is configured."""
    configured = (get_settings().office_lookup_token or "").strip()
    if not configured:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Category feed is disabled (no token set).")
    if not token or not hmac.compare_digest(token.strip(), configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing token.")


@router.get("/enabled")
async def enabled_categories(
    x_office_token: str | None = Header(default=None, alias="X-Office-Token"),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """The enabled category set: {ids, slugs, count, cached_at, unmatched_slugs}.

    200 means the list is authoritative — an EMPTY list means the owner has genuinely enabled
    nothing, and the storefront must show nothing rather than dropping its filter. 503 means the
    panel could not resolve visibility at all; the storefront keeps its last stored set.

    Those two must never look alike. The whole gate rests on the storefront being able to tell
    "nothing is enabled" from "I do not know what is enabled", and the only way it can is if the
    failure never arrives dressed as a successful empty answer.
    """
    _check_token(x_office_token)
    try:
        return await build_enabled_feed(session)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — every failure becomes 503, never an empty 200
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Category visibility could not be resolved; keep your last known set. "
            f"({type(e).__name__})",
        ) from e
