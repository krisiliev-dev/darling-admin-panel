# -*- coding: utf-8 -*-
"""Two-factor authentication (TOTP) — owner decision 2026-08-05: MANDATORY for every role.

Shipped in two phases because 2FA is the one surface where a half-finished deploy locks everyone
(including the owner) out of a LIVE store:

  PHASE 1 — enrollment only (`403e4e8`): setup → verify → enabled, recovery codes, owner reset.
    Login untouched, so it could not strand anyone. Verified in prod before phase 2 was written.
  PHASE 2 — this file's `gate_router` + `/2fa/login`: the login GATE. Both login paths stop
    short of issuing the session cookie for an ENROLLED user and hand back a short-lived ticket;
    the cookie is only issued once a TOTP or recovery code is presented.

Rollout safety (deliberate, not an oversight):
  * The gate triggers on `totp_enabled` — NOT on role. A user who has not enrolled logs in
    exactly as before, so switching this on cannot lock anyone out. „Mandatory" is enforced by
    the frontend wall (un-enrolled users can reach only Настройки → Сигурност), which is
    reversible from the server side at any moment. Hard server-side refusal comes only after
    everyone is enrolled — that is a one-line change, made deliberately, not today.
  * The owner's own lost-device path is a documented manual DB step; no endpoint can strip the
    owner's second factor.
"""
from __future__ import annotations

import json
import secrets
import uuid

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ratelimit
from ..audit import log_action
from ..config import get_settings
from ..db import get_async_session
from ..models import User
from ..users import (
    UserManager,
    auth_backend,
    current_active_user,
    get_jwt_strategy,
    get_user_manager,
    require_roles,
)

router = APIRouter(prefix="/api/auth/2fa", tags=["auth-2fa"])
# Registered BEFORE fastapi-users' auth router in main.py so this /api/auth/login wins the match.
gate_router = APIRouter(prefix="/api/auth", tags=["auth"])

_pw = PasswordHelper()
_ISSUER = "Дарлинг панел"
N_RECOVERY = 10
TICKET_TTL = 300                     # seconds — long enough to fetch a code, short enough to sting
_TICKET_AUD = "darling:2fa-ticket"

# Consumed ticket ids. A ticket is single-use: without this a captured ticket could be replayed
# for its whole TTL with a second stolen code. Process-local is honest here — the panel runs as a
# single container, and a restart invalidating tickets is a safe failure (users just log in again).
_used_tickets: set[str] = set()


def _qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    return img.to_string(encoding="unicode")


def _gen_recovery() -> tuple[list[str], str]:
    """Plaintext codes (shown once) + JSON of their hashes (stored)."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(N_RECOVERY)]
    return codes, json.dumps([_pw.hash(c) for c in codes])


def make_ticket(user: User) -> str:
    """Short-lived, single-purpose token proving 'password/Barsy factor already passed'."""
    return generate_jwt(
        {"sub": str(user.id), "aud": _TICKET_AUD, "jti": uuid.uuid4().hex},
        get_settings().jwt_secret, lifetime_seconds=TICKET_TTL)


def _read_ticket(ticket: str) -> tuple[str, str]:
    try:
        data = decode_jwt(ticket, get_settings().jwt_secret, audience=[_TICKET_AUD])
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Изтекла или невалидна заявка за вход. Влезте отново.") from None
    sub, jti = str(data.get("sub") or ""), str(data.get("jti") or "")
    if not sub or not jti:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Невалидна заявка за вход.")
    return sub, jti


def _burn_recovery(user: User, code: str) -> bool:
    """Verify a recovery code and REMOVE it (one-time). True if it matched."""
    try:
        hashes = json.loads(user.totp_recovery or "[]")
    except Exception:
        return False
    for h in list(hashes):
        ok, _ = _pw.verify_and_update(code, h)
        if ok:
            hashes.remove(h)
            user.totp_recovery = json.dumps(hashes)
            return True
    return False


class VerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TwoFactorLoginIn(BaseModel):
    ticket: str = Field(min_length=10, max_length=2000)
    code: str = Field(default="", max_length=20)
    recovery_code: str = Field(default="", max_length=40)


# ------------------------------------------------------------------ enrollment (phase 1) ---
@router.get("/status")
async def status_(user: User = Depends(current_active_user)) -> dict:
    remaining = 0
    if user.totp_recovery:
        try:
            remaining = len(json.loads(user.totp_recovery))
        except Exception:
            remaining = 0
    return {"enabled": bool(user.totp_enabled), "recovery_remaining": remaining, "mandatory": True}


@router.post("/setup")
async def setup(user: User = Depends(current_active_user),
                session: AsyncSession = Depends(get_async_session)) -> dict:
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
async def owner_reset(user_id: str, actor: User = Depends(require_roles("owner")),
                      session: AsyncSession = Depends(get_async_session)) -> dict:
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
    await log_action(session, actor, "2fa.reset", target=target.email, detail="owner cleared 2fa")
    return {"ok": True, "email": target.email}


# ------------------------------------------------------------------ the gate (phase 2) ---
@router.post("/login")
async def twofa_login(body: TwoFactorLoginIn, request: Request,
                      session: AsyncSession = Depends(get_async_session)):
    """Second factor: exchange a ticket + TOTP (or a recovery code) for the session cookie."""
    sub, jti = _read_ticket(body.ticket)
    if jti in _used_tickets:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Тази заявка за вход вече е използвана. Влезте отново.")
    ratelimit.guard_login(request, f"2fa-login:{sub}")
    user = await session.get(User, sub)
    if not user or not user.is_active or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Невалидна заявка за вход.")

    used_recovery = False
    code = body.code.strip()
    recovery = body.recovery_code.strip()
    if code and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        pass
    elif recovery and _burn_recovery(user, recovery):
        used_recovery = True
    else:
        ratelimit.record_login_failure(request, f"2fa-login:{sub}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Грешен код.")

    _used_tickets.add(jti)
    if len(_used_tickets) > 5000:            # bound the set; tickets expire in 5 min anyway
        _used_tickets.clear()
    ratelimit.reset_login(request, f"2fa-login:{sub}")
    await session.commit()
    await log_action(session, user, "2fa.login", target=user.email,
                     detail="recovery code used" if used_recovery else "totp")
    resp = await auth_backend.login(get_jwt_strategy(), user)
    if used_recovery:
        # not fatal, but the UI should nudge: one of the ten is now gone
        resp.headers["X-2FA-Recovery-Used"] = "1"
    return resp


@gate_router.post("/login")
async def login_with_2fa_gate(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Replaces fastapi-users' /api/auth/login (registered before it in main.py).

    Identical behaviour for everyone who has NOT enrolled — same 204 + cookie, so no existing
    client or test changes. For an ENROLLED user it stops one step short and returns a ticket
    instead of the cookie."""
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "LOGIN_BAD_CREDENTIALS")
    if user.totp_enabled:
        return JSONResponse({"twofa_required": True, "ticket": make_ticket(user)})
    return await auth_backend.login(get_jwt_strategy(), user)
