# -*- coding: utf-8 -*-
"""First-run owner setup + a couple of auth conveniences that sit alongside the
standard FastAPI-Users routers."""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from .. import barsy_auth, ratelimit
from ..audit import log_action
from ..config import get_settings
from ..db import get_async_session
from ..models import User
from ..schemas import OwnerSetup, SetupStatus, UserCreate, UserRead
from ..users import UserManager, current_sysadmin, get_jwt_strategy, get_user_manager

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _user_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


@router.get("/setup-status", response_model=SetupStatus)
async def setup_status(session: AsyncSession = Depends(get_async_session)):
    """The login screen calls this: with zero users, show the one-time owner setup
    instead of the login form."""
    return SetupStatus(needs_setup=(await _user_count(session)) == 0)


@router.post("/setup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_owner(
    payload: OwnerSetup,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Create the first owner. Allowed ONCE — refused the moment any user exists, so
    this can never be used to mint a second admin from the open internet."""
    if (await _user_count(session)) > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Панелът вече е настроен. Влезте или помолете собственика за достъп.")
    try:
        owner = await user_manager.create(UserCreate(
            email=payload.email, password=payload.password,
            full_name=payload.full_name, role="owner", is_superuser=True,
        ))
    except UserAlreadyExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Този имейл вече съществува.")
    except IntegrityError:
        # the single-owner partial index fired — another /setup won the race
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Панелът вече има собственик. Влезте вместо това.")
    await log_action(session, owner, "owner.setup", target=owner.email,
                     detail="Първоначална настройка на панела.")
    return owner


class BarsyLogin(BaseModel):
    username: str
    password: str


@router.post("/barsy-login", response_model=UserRead)
async def barsy_login(
    body: BarsyLogin,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    """Log in with BARSY credentials. Verifies against Barsy (Users_getcurrent), mirrors the
    user locally with their Barsy role, and issues the panel session cookie. No password stored.
    Rate-limited: per-ip cap (middleware) + per-(ip,user) failed-attempt lockout (below)."""
    ratelimit.guard_login(request, body.username)
    try:
        profile = await run_in_threadpool(barsy_auth.verify, body.username, body.password)
    except RuntimeError as e:
        # this path is UNAUTHENTICATED — don't reflect upstream transport detail (status codes,
        # host fragments) to the caller. Log the cause; return a generic 503.
        logging.getLogger("darling.auth").warning("Barsy verify failed: %s", e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Barsy е временно недостъпен. Опитайте отново.")
    if not profile:
        ratelimit.record_login_failure(request, body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Грешно потребителско име или парола.")
    ratelimit.reset_login(request, body.username)  # a correct login clears the lockout counter

    role, is_superuser = barsy_auth.map_role(profile)
    # synthesize a VALID email if Barsy has none (barsy.online is a real domain; EmailStr rejects
    # reserved TLDs like .local). Kept lowercase + unique per Barsy username.
    email = (profile.get("email") or f"{body.username}@barsy.online").strip().lower()
    full_name = (f"{profile.get('fname', '')} {profile.get('lname', '')}".strip()
                 or profile.get("user_name") or body.username)

    # upsert the local mirror (keyed by email); Barsy stays the source of truth for role
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()  # type: ignore[arg-type]  # SQLAlchemy/fastapi-users stub gap, not a bug
    if user is not None and user.role == "owner":
        # never let a Barsy login demote/clobber the local break-glass owner
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Този имейл принадлежи на служебния акаунт — влез със служебен вход.")
    if user is None:
        user = User(email=email, hashed_password=PasswordHelper().hash(secrets.token_urlsafe(32)),
                    is_active=True, is_verified=True)
        session.add(user)
    user.full_name = full_name
    user.role = role
    user.is_superuser = is_superuser
    try:
        await session.commit()
    except IntegrityError:
        # two concurrent first-logins of the same Barsy user raced on the unique email — the other
        # won; roll back and adopt the row it created rather than 500.
        await session.rollback()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()  # type: ignore[arg-type]  # SQLAlchemy/fastapi-users stub gap, not a bug
    await session.refresh(user)
    await log_action(session, user, "barsy.login", target=email, detail=f"role={role}")

    # 2FA gate (phase 2): an ENROLLED user does NOT get the session cookie here - they get a
    # short-lived ticket and must present a TOTP or recovery code at /api/auth/2fa/login. Users
    # who have not enrolled are entirely unaffected, which is what makes switching this on safe.
    # Local import keeps module import order free of any auth_extra <-> twofa cycle.
    if user.totp_enabled:
        from .twofa import make_ticket
        return JSONResponse({"twofa_required": True, "ticket": make_ticket(user)})

    # issue the panel JWT cookie (same cookie the rest of the app expects)
    settings = get_settings()
    token = await get_jwt_strategy().write_token(user)
    resp = JSONResponse(UserRead.model_validate(user).model_dump(mode="json"))
    resp.set_cookie("darling_admin", token, max_age=settings.jwt_lifetime_seconds,
                    httponly=True, samesite="lax", secure=settings.cookie_secure)
    return resp


@router.post("/staff", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_staff(
    payload: UserCreate,
    actor: User = Depends(current_sysadmin),  # owner-only
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Owner adds a staff or packer account. Owners are minted only via first-run setup."""
    if payload.role not in ("staff", "packer"):
        # only these two are mintable here; owner is first-run-only and manager comes from Barsy
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Ролята трябва да е 'staff' или 'packer'.")
    payload.is_superuser = False
    try:
        u = await user_manager.create(payload)
    except UserAlreadyExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Този имейл вече съществува.")
    await log_action(session, actor, "staff.create", target=u.email,
                     detail=f"роля={u.role}")
    return u
