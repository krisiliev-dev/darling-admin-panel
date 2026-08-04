# -*- coding: utf-8 -*-
"""Клиенти — panel CRUD over Barsy clients. Barsy is the database; this is a tailored face over it.

Owner/manager only (client rows carry PII and pricing terms). Writes go straight to Barsy — client
create/edit is NOT behind the registered-каса gate (verified live: a plain session created client
40), unlike client ORDERS which must route through the connector identity (#82).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..models import User
from ..services import barsy_clients as bc
from ..services.barsy_clients import BarsyClientError
from ..users import current_owner

router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientIn(BaseModel):
    client_name: str
    tel: str | None = None
    email: str | None = None
    group_id: int | None = None
    discount: str = "0"
    credit_limit: str | None = None
    company_name: str | None = None
    bulstat: str | None = None
    vat_num: str | None = None
    mol: str | None = None
    email_invoices: str | None = None
    receiver_name: str | None = None
    town: str | None = None
    address: str | None = None
    zip: str | None = None
    description: str | None = None
    delete_flag: int | None = None


@router.get("")
async def list_clients(actor: User = Depends(current_owner)) -> dict:
    return {"clients": await run_in_threadpool(bc.list_clients)}


@router.get("/groups")
async def client_groups(actor: User = Depends(current_owner)) -> dict:
    return {"groups": await run_in_threadpool(bc.groups)}


@router.get("/{client_id}")
async def get_client(client_id: int, actor: User = Depends(current_owner)) -> dict:
    values = await run_in_threadpool(bc.read_values, client_id)
    if not values:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиентът не е намерен.")
    return {"client": values}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client(req: ClientIn, actor: User = Depends(current_owner)) -> dict:
    if not req.client_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дай име на клиента.")
    try:
        cid = await run_in_threadpool(bc.create, req.model_dump(exclude_unset=True))
    except BarsyClientError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {e}")
    return {"client_id": cid, "created": True}


@router.put("/{client_id}")
async def update_client(client_id: int, req: ClientIn,
                        actor: User = Depends(current_owner)) -> dict:
    if not req.client_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Дай име на клиента.")
    try:
        cid = await run_in_threadpool(bc.update, client_id, req.model_dump(exclude_unset=True))
    except BarsyClientError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {e}")
    return {"client_id": cid, "updated": True}


@router.delete("/{client_id}")
async def delete_client(client_id: int, actor: User = Depends(current_owner)) -> dict:
    try:
        await run_in_threadpool(bc.delete, client_id)
    except BarsyClientError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Barsy: {e}")
    return {"client_id": client_id, "deleted": True}
