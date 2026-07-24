# -*- coding: utf-8 -*-
"""Pydantic schemas for the auth surface."""
import uuid

from fastapi_users import schemas
from pydantic import BaseModel, EmailStr


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str = ""
    role: str = "staff"


class UserCreate(schemas.BaseUserCreate):
    full_name: str = ""
    role: str = "staff"


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None
    # SECURITY: `role` is deliberately NOT here. FastAPI-Users' self-service /me PATCH does a
    # "safe" update that only strips {id, is_superuser, is_active, is_verified}; a custom `role`
    # field would slip through, letting any user (even a packer) set their own role and pass
    # require_roles() gates. Roles are set only at creation (create_staff) or by Barsy. If an
    # owner ever needs to re-role a local account, add an explicit current_owner-guarded route.


class OwnerSetup(BaseModel):
    """First-run one-time owner creation."""
    email: EmailStr
    password: str
    full_name: str = ""


class SetupStatus(BaseModel):
    needs_setup: bool
