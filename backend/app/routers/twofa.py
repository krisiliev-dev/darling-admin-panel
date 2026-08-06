# -*- coding: utf-8 -*-
"""Two-factor authentication (TOTP) — owner decision 2026-08-05: MANDATORY for every role.

Built in two deliberate phases because 2FA is the one surface where a half-finished deploy can
lock everyone (including the owner) out of a LIVE store:

  PHASE 1 (this file): ENROLLMENT ONLY. setup → verify → enabled, recovery codes, owner reset,
    status. NOTHING here changes how login works, so shipping it cannot lock anyone out.
  PHASE 2 (separate, after phase 1 is verified in prod): the login GATE — /api/auth/login and
    /api/auth/barsy-login return a 2fa ticket instead of a cookie for enrolled users, and
    /api/auth/2fa/login burns a code to finish. A soft-enforce wall (frontend) sends un-enrolled
    users to Настройки → Сигурност until they enroll, so nobody is stranded on rollout day.

Secrets: totp_pending_secret holds an UNCONFIRMED secret between setup and verify; only a proven
code promotes it to totp_secret + totp_enabled. Recovery codes are stored argon2-hashed (same
helper the passwords use); shown to the user exactly once.
"""
from __future__ import annotations

import json
import secrets

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ratelimit
from ..audit import log_action
from ..db import get_async_session
from ..models import User
from ..users import current_active_user, require_roles

router = APIRouter(prefix="/api/auth/2fa", tags=["auth-2fa"])
_pw = PasswordHelper()
_ISSUER = "Дарлинг панел"
N_RECOVERY = 10


def _qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    return img.to_string(encoding="unicode")


def _gen_recovery() -> tuple[list[str], str]:
    """Plaintext codes (shown once) + the JSON of their argon2 hashes (stored)."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(N_RECOVERY)]
    stored = json.dumps([_pw.hash(c) for c in codes])
    return codes, stored


class VerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


@router.get("/status")
async def status_(user: User = Depends(current_active_user)) -> dict:
    remaining = 0
    if user.totp_recovery:
        try:
            remaining = len(json.loads(user.totp_recovery))
        except Exception:
            remaining = 0
    return {"enabled": bool(user.totp_enabled), "recovery_remaining": remaining,
            "mandatory": True}


@router.post("/setup")
async def setup(user: User = Depends(current_active_user),
                session: AsyncSession = Depends(get_async_session)) -> dict:
    """Begin enrollment: mint an UNCONFIRMED secret, return the otpauth URI + a QR SVG. Idempotent
    — calling again before verify rotates the pending secret (nothing is enabled yet)."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Двуфакторното удостоверяване вече е включено.")
    secret = pyotp.random_base32()
    user.totp_pending_secret = secret
    await session.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=_ISSUER)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


@router.post("/verify")
async def verify(body: VerifyIn, request: Request,
                 user: User = Depends(current_active_user),
                 session: AsyncSession = Depends(get_async_session)) -> dict:
    """Prove a code against the pending secret → enable 2FA and return the one-time recovery codes
    (shown ONCE). Rate-limited so the 6-digit space can't be brute-forced during enrollment."""
    ratelimit.guard_login(request, f"2fa-setup:{user.email}")
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Вече е включено.")
    if not user.totp_pending_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Първо стартирайте настройката (/setup).")
    if not pyotp.TOTP(user.totp_pending_secret).verify(body.code.strip(), valid_window=1):
        ratelimit.record_login_failure(request, f"2fa-setup:{user.email}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Грешен код. Опитайте отново.")
    ratelimit.reset_login(request, f"2fa-setup:{user.email}")
    codes, stored = _gen_recovery()
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_enabled = True
    user.totp_recovery = stored
    await session.commit()
    await log_action(session, user, "2fa.enable", target=user.email, detail="totp enrolled")
    return {"enabled": True, "recovery_codes": codes,
            "note": "Запишете кодовете за възстановяване сега — показват се само веднъж."}


@router.post("/recovery/regenerate")
async def regenerate(user: User = Depends(current_active_user),
                     session: AsyncSession = Depends(get_async_session)) -> dict:
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA не е включено.")
    codes, stored = _gen_recovery()
    user.totp_recovery = stored
    await session.commit()
    await log_action(session, user, "2fa.recovery_regen", target=user.email)
    return {"recovery_codes": codes}


@router.post("/reset/{user_id}")
async def owner_reset(user_id: str,
                      actor: User = Depends(require_roles("owner")),
                      session: AsyncSession = Depends(get_async_session)) -> dict:
    """Owner clears a user's 2FA (lost phone). The target must re-enroll on next login. The owner's
    OWN lost-device path is a documented manual DB step — the owner is the last line, no self-reset
    endpoint that could be abused to strip the owner's second factor."""
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Няма такъв потребител.")
    if target.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Собственическият акаунт се възстановява ръчно (по документация), не оттук.")
    target.totp_enabled = False
    target.totp_secret = None
    target.totp_pending_secret = None
    target.totp_recovery = None
    await session.commit()
    await log_action(session, actor, "2fa.reset", target=target.email,
                     detail="owner cleared 2fa")
    return {"ok": True, "email": target.email}
