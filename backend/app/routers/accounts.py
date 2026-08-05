# -*- coding: utf-8 -*-
"""Сметки — read-only browser over Barsy accounts (Продажби по сметки). Barsy is the database; this
is a tailored, read-only face over it. Owner/manager only (sales/financial data). No writes — the
account/sale lifecycle stays in Barsy. Feeds #72 УНП capture (each account carries its sale identity).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

from ..models import User
from ..services import barsy_accounts as ba
from ..users import current_owner

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
async def list_accounts(actor: User = Depends(current_owner),
                        limit: int = Query(100, ge=1, le=500)) -> dict:
    return {"accounts": await run_in_threadpool(ba.list_accounts, limit)}


@router.get("/{account_id}")
async def get_account(account_id: int, actor: User = Depends(current_owner)) -> dict:
    a = await run_in_threadpool(ba.get_account, account_id)
    if not a or not a.get("account_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сметката не е намерена.")
    return {"account": a}
