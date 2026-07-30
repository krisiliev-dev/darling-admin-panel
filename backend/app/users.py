# -*- coding: utf-8 -*-
"""FastAPI-Users wiring: cookie+JWT auth backend, user manager, and role guards."""
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_async_session
from .models import User

_settings = get_settings()


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = _settings.jwt_secret
    verification_token_secret = _settings.jwt_secret

    async def validate_password(self, password: str, user) -> None:
        # minimal strength for the local (break-glass) accounts; Barsy users never set a
        # password here (Barsy generates + emails it)
        if len(password) < 8:
            from fastapi_users.exceptions import InvalidPasswordException
            raise InvalidPasswordException("Паролата трябва да е поне 8 знака.")

    async def on_after_register(self, user: User, request=None):
        # keep is_superuser in lockstep with the owner role
        if user.role == "owner" and not user.is_superuser:
            await self.user_db.update(user, {"is_superuser": True})

    async def on_after_login(self, user: User, request=None, response=None):
        # audit WHO logged in — the login request carries no cookie yet, so the middleware can't
        # attribute it; this captures the identity. (Failed logins are logged by the middleware.)
        from .audit import log_action
        from .db import async_session_maker
        try:
            async with async_session_maker() as s:
                await log_action(s, user, "auth.login", target=user.email)
        except Exception:
            pass


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


# --- auth backend: httponly cookie carrying a JWT -----------------------------------
cookie_transport = CookieTransport(
    cookie_name="darling_admin",
    cookie_max_age=_settings.jwt_lifetime_seconds,
    cookie_secure=_settings.cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=_settings.jwt_secret, lifetime_seconds=_settings.jwt_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_owner = fastapi_users.current_user(active=True, superuser=True)


def require_roles(*roles: str):
    """Dependency factory: allow only listed roles (any superuser — owner/manager — always passes)."""
    async def _guard(user: User = Depends(current_active_user)) -> User:
        if user.is_superuser or user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Няма достъп до тази функция.")
    return _guard


async def current_owner_strict(user: User = Depends(current_active_user)) -> User:
    """Literally the owner — `role == "owner"`, NOT merely a superuser.

    `current_owner` is fastapi-users' superuser check, and Barsy managers map to superuser, so it does
    not mean "the owner". For granting the sysadmin capability that distinction is the whole point: a
    manager must not be able to hand themselves the system access this design withholds from them."""
    if user.role == "owner":
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN,
                        "Само собственикът може да променя системния достъп.")


async def current_sysadmin(user: User = Depends(current_active_user)) -> User:
    """SYSTEM-level guard: the owner, or anyone holding the sysadmin capability.

    Distinct from `current_owner` (= any superuser, which includes Barsy managers) on purpose. Owner
    decision 2026-07-30: system work — write gates, integrations/webhook config, user administration,
    audit and logs — is a different axis from business authority, so a Мениджър with full authority over
    orders, pricing and Склад does NOT automatically get to change integration wiring or add accounts.
    Note this deliberately NARROWS the 2026-07-20 decision; see the note in models.User.is_sysadmin.

    `role == "owner"` (not is_superuser) is the escape hatch, so applying this can never lock the owner
    out of their own panel before anyone has been granted the capability."""
    if user.role == "owner" or user.is_sysadmin:
        return user
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Тази част е само за системен администратор. Обърни се към собственика за достъп.")
