"""Focused tests for the Phase 1 authority lookup seam."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.authority import fetch_profile_state, fetch_tenant_state


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_authority_resolves_profile_payload():
    response = _FakeResponse(
        200,
        [{"role": "admin", "tenant_id": "tenant-1", "is_active": True}],
    )

    with patch("api.authority.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
        profile = await fetch_profile_state("user-1")

    assert profile == {"role": "admin", "tenant_id": "tenant-1", "is_active": True}


@pytest.mark.asyncio
async def test_authority_returns_none_for_missing_profile():
    response = _FakeResponse(200, [])

    with patch("api.authority.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
        profile = await fetch_profile_state("user-1")

    assert profile is None


@pytest.mark.asyncio
async def test_authority_resolves_tenant_state():
    response = _FakeResponse(200, [{"id": "tenant-1", "is_active": True}])

    with patch("api.authority.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
        tenant = await fetch_tenant_state("tenant-1")

    assert tenant == {"id": "tenant-1", "is_active": True}


@pytest.mark.asyncio
async def test_authority_returns_none_for_missing_tenant():
    response = _FakeResponse(200, [])

    with patch("api.authority.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
        tenant = await fetch_tenant_state("tenant-1")

    assert tenant is None
