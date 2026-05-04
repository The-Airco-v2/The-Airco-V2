"""Backend auth endpoints for session-cookie bootstrap."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from api import auth as auth_service

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


async def _ensure_tenant(db: AsyncSession, tenant_id: str) -> None:
    """Upsert the tenant row into the local DB so FK constraints are satisfied."""
    await db.execute(
        text(
            "INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "name": tenant_id},
    )
    await db.commit()


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_session)):
    user = await auth_service.authenticate_password_login(body.email, body.password)
    payload = await auth_service.build_account_state_payload(
        user_id=str(user["user_id"]),
        email=user.get("email"),
    )
    response.status_code = auth_service.account_state_status_code(payload["accountState"])

    session_payload = auth_service.build_session_payload_from_account_payload(payload)
    if session_payload is None:
        auth_service.clear_session_cookie(response)
    else:
        auth_service.set_session_cookie(response, session_payload)
        # Ensure tenant row exists in local DB so FK constraints on business tables are satisfied.
        tenant_id = payload.get("tenantId")
        if isinstance(tenant_id, str) and tenant_id:
            try:
                await _ensure_tenant(db, tenant_id)
            except Exception:
                logger.warning("Failed to upsert tenant %s into local DB", tenant_id, exc_info=True)

    return payload


@router.post("/logout")
async def logout(response: Response):
    auth_service.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(request: Request, response: Response):
    session = auth_service.read_session_cookie(request)
    user_id = session.get("user_id") if session else None
    email = session.get("email") if session else None
    if not isinstance(user_id, str) or not user_id:
        payload = auth_service.build_unauthenticated_payload()
        response.status_code = 401
        auth_service.clear_session_cookie(response)
        return payload

    payload = await auth_service.build_account_state_payload(
        user_id=user_id,
        email=email if isinstance(email, str) else None,
    )
    response.status_code = auth_service.account_state_status_code(payload["accountState"])
    if payload["accountState"] != "authenticated":
        auth_service.clear_session_cookie(response)
    return payload
