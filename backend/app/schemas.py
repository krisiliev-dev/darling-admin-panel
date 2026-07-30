# -*- coding: utf-8 -*-
"""Pydantic schemas for the auth surface."""
import uuid

from fastapi_users import schemas
from pydantic import BaseModel, EmailStr


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str = ""
    role: str = "staff"
    # read-only here: the frontend needs it to decide which system surfaces to render
    is_sysadmin: bool = False


class UserCreate(schemas.BaseUserCreate):
    full_name: str = ""
    role: str = "staff"


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None
    # SECURITY: `role` is deliberately NOT here, and neither is `is_sysadmin`. FastAPI-Users'
    # self-service /me PATCH does a "safe" update that only strips {id, is_superuser, is_active,
    # is_verified}; any custom field we declare slips through — so declaring either would let a user
    # grant themselves the role or the system capability and pass every gate that checks them.
    # Roles are set at creation (create_staff) or by Barsy; is_sysadmin only via the owner-guarded
    # /api/admin/sysadmin route.


class OwnerSetup(BaseModel):
    """First-run one-time owner creation."""
    email: EmailStr
    password: str
    full_name: str = ""


class SetupStatus(BaseModel):
    needs_setup: bool
