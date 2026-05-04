"""Supabase-backed authority lookup seam for Phase 1 auth flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from airco.config import settings


@dataclass
class AuthorityLookupError(Exception):
    code: str
    message: str
    status_code: int = 401


def _service_headers() -> dict[str, str]:
    token = settings.supabase_service_role_key
    return {
        "apikey": token,
        "Authorization": f"Bearer {token}",
    }


async def _fetch_rows(
    *,
    table: str,
    select: str,
    match_field: str,
    match_value: str,
    error_code: str,
    error_message: str,
) -> list[dict[str, Any]]:
    encoded_value = quote(str(match_value), safe="")
    url = (
        f"{settings.supabase_url}/rest/v1/{table}"
        f"?{match_field}=eq.{encoded_value}&select={select}"
    )

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers=_service_headers(),
            timeout=5.0,
        )

    if response.status_code != 200:
        raise AuthorityLookupError(code=error_code, message=error_message)

    payload = response.json()
    return payload if isinstance(payload, list) else []


async def fetch_profile_state(user_id: str) -> dict[str, Any] | None:
    rows = await _fetch_rows(
        table="user_profiles",
        select="role,tenant_id,is_active",
        match_field="id",
        match_value=user_id,
        error_code="auth_profile_lookup_failed",
        error_message="Could not verify user profile",
    )
    return rows[0] if rows else None


async def fetch_tenant_state(tenant_id: str) -> dict[str, Any] | None:
    rows = await _fetch_rows(
        table="tenants",
        select="id,is_active",
        match_field="id",
        match_value=tenant_id,
        error_code="auth_tenant_lookup_failed",
        error_message="Could not verify tenant",
    )
    return rows[0] if rows else None
