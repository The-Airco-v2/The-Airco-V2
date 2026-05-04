"""Focused tests for auth helpers — no live Supabase needed."""

from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi import HTTPException, Response

from airco.config import settings

import api.auth as auth_module
from api.auth import (
    AuthState,
    build_session_cookie,
    clear_session_cookie,
    decode_session_cookie,
    require_authenticated,
    set_session_cookie,
)


def _active_auth_state(*, role: str) -> AuthState:
    return AuthState(
        user_id="u1",
        role=role,
        tenant_id="default",
        profile_is_active=True,
        tenant_is_active=True,
    )


def _session_payload() -> dict:
    return {
        "user_id": "user-1",
        "email": "ops@example.com",
        "tenant_id": "tenant-1",
        "role": "admin",
        "account_state": "authenticated",
        "profile_is_active": True,
        "tenant_is_active": True,
    }


class _FakeAsyncClient:
    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response

    async def post(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


class _FakeResponse:
    def __init__(self, payload, *, status_code=200, text="", json_error=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self._json_error = json_error

    def raise_for_status(self):
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


@pytest.mark.asyncio
async def test_require_authenticated_missing_header():
    with pytest.raises(HTTPException) as exc:
        await require_authenticated(authorization=None)
    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "auth_authorization_header_missing",
        "message": "Authorization header required (Bearer token)",
    }


@pytest.mark.asyncio
async def test_require_authenticated_bad_prefix():
    with pytest.raises(HTTPException) as exc:
        await require_authenticated(authorization="Token abc123")
    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "auth_authorization_header_missing",
        "message": "Authorization header required (Bearer token)",
    }


@pytest.mark.asyncio
async def test_require_authenticated_invalid_token_claims():
    with patch("api.auth.verify_jwt", new=AsyncMock(return_value={})):
        with pytest.raises(HTTPException) as exc:
            await require_authenticated(authorization="Bearer abc123")

    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "auth_invalid_token_claims",
        "message": "Invalid token claims",
    }


@pytest.mark.asyncio
async def test_require_admin_rejects_viewer():
    viewer = _active_auth_state(role="viewer")
    with pytest.raises(HTTPException) as exc:
        await auth_module.require_admin(auth=viewer)
    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "auth_insufficient_permissions",
        "message": "Admin access required",
    }


@pytest.mark.asyncio
async def test_require_admin_accepts_admin():
    admin = _active_auth_state(role="admin")
    result = await auth_module.require_admin(auth=admin)
    assert result.role == "admin"


@pytest.mark.asyncio
async def test_get_public_key_selects_jwk_matching_token_kid():
    auth_module._jwks_cache = None

    with (
        patch("api.auth.jwt.get_unverified_header", return_value={"kid": "kid-2"}),
        patch(
            "api.auth._fetch_jwks",
            new=AsyncMock(
                return_value={
                    "keys": [
                        {"kid": "kid-1", "kty": "EC"},
                        {"kid": "kid-2", "kty": "EC"},
                    ]
                }
            ),
        ),
        patch("api.auth.ECAlgorithm.from_jwk", return_value="public-key") as from_jwk,
    ):
        result = await auth_module._get_public_key("header.payload.signature")

    assert result == "public-key"
    from_jwk.assert_called_once_with('{"kid": "kid-2", "kty": "EC"}')
    auth_module._jwks_cache = None


@pytest.mark.asyncio
async def test_fetch_jwks_maps_http_failure_to_auth_error():
    with patch(
        "api.auth.httpx.AsyncClient",
        return_value=_FakeAsyncClient(error=httpx.ConnectError("boom")),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module._fetch_jwks()

    assert exc.value.status_code == 502
    assert exc.value.detail == {
        "code": "auth_jwks_fetch_failed",
        "message": "Could not verify token signing keys",
    }


@pytest.mark.asyncio
async def test_fetch_jwks_maps_invalid_payload_to_auth_error():
    with patch(
        "api.auth.httpx.AsyncClient",
        return_value=_FakeAsyncClient(response=_FakeResponse({"unexpected": []})),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module._fetch_jwks()

    assert exc.value.status_code == 502
    assert exc.value.detail == {
        "code": "auth_jwks_invalid",
        "message": "Could not verify token signing keys",
    }


@pytest.mark.asyncio
async def test_verify_jwt_preserves_expired_token_mapping_after_jwks_refresh():
    auth_module._jwks_cache = {"keys": [{"kid": "stale", "kty": "EC"}]}

    with (
        patch("api.auth._get_public_key", new=AsyncMock(side_effect=["stale-key", "fresh-key"])),
        patch(
            "api.auth.jwt.decode",
            side_effect=[jwt.InvalidSignatureError("stale"), jwt.ExpiredSignatureError("expired")],
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module.verify_jwt("header.payload.signature")

    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "auth_token_expired",
        "message": "Token expired",
    }
    assert auth_module._jwks_cache is None


@pytest.mark.asyncio
async def test_authenticate_password_login_maps_rate_limit_to_retryable_auth_error(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://supabase.example.com")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon-key")

    with patch(
        "api.auth.httpx.AsyncClient",
        return_value=_FakeAsyncClient(
            response=_FakeResponse(
                {"error": "rate_limit"},
                status_code=429,
                text="too many requests",
            )
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module.authenticate_password_login("ops@example.com", "secret")

    assert exc.value.status_code == 429
    assert exc.value.detail == {
        "code": "auth_login_rate_limited",
        "message": "Too many login attempts. Please try again later.",
    }


@pytest.mark.asyncio
async def test_authenticate_password_login_maps_invalid_json_to_controlled_auth_error(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://supabase.example.com")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon-key")

    with patch(
        "api.auth.httpx.AsyncClient",
        return_value=_FakeAsyncClient(
            response=_FakeResponse(
                None,
                status_code=200,
                text="not-json",
                json_error=ValueError("invalid json"),
            )
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module.authenticate_password_login("ops@example.com", "secret")

    assert exc.value.status_code == 502
    assert exc.value.detail == {
        "code": "auth_invalid_upstream_response",
        "message": "Authentication service returned an invalid response",
    }


def test_session_cookie_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")

    cookie_value = build_session_cookie(_session_payload())
    decoded = decode_session_cookie(cookie_value)

    assert decoded is not None
    assert decoded["user_id"] == "user-1"
    assert decoded["email"] == "ops@example.com"
    assert decoded["tenant_id"] == "tenant-1"
    assert decoded["role"] == "admin"
    assert decoded["account_state"] == "authenticated"
    assert decoded["profile_is_active"] is True
    assert decoded["tenant_is_active"] is True
    assert decoded["exp"] > decoded["iat"]


def test_session_cookie_rejects_tampering(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")

    cookie_value = build_session_cookie(_session_payload())
    tampered = f"{cookie_value[:-1]}x"

    assert decode_session_cookie(tampered) is None


def test_clear_session_cookie_sets_removal_header(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_name", "airco_session")
    response = Response()

    clear_session_cookie(response)

    set_cookie_header = response.headers["set-cookie"]
    assert "airco_session=" in set_cookie_header
    assert "Max-Age=0" in set_cookie_header
    assert "HttpOnly" in set_cookie_header


def test_set_session_cookie_uses_configured_cookie_settings(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_cookie_name", "airco_session")
    monkeypatch.setattr(settings, "session_ttl_seconds", 1234)
    monkeypatch.setattr(settings, "session_secure_cookie", False)
    monkeypatch.setattr(settings, "session_same_site", "strict")
    response = Response()

    set_session_cookie(response, _session_payload())

    set_cookie_header = response.headers["set-cookie"]
    assert "airco_session=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header
    assert "Max-Age=1234" in set_cookie_header
    assert "SameSite=strict" in set_cookie_header
    assert "Secure" not in set_cookie_header
