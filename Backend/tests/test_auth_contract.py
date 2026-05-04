"""Auth contract tests for explicit account-state handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from airco.config import settings
from api.auth import AuthContext, build_account_state_payload, build_session_cookie, require_authenticated
from api.main import app


def _authenticated_session_payload() -> dict:
    return {
        "user_id": "user-1",
        "email": "ops@example.com",
        "tenant_id": "tenant-from-session",
        "role": "viewer",
        "account_state": "authenticated",
        "profile_is_active": True,
        "tenant_is_active": True,
    }


def _request_with_session_cookie(monkeypatch, session_payload: dict | None = None):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")

    cookies = {}
    if session_payload is not None:
        cookies[settings.session_cookie_name] = build_session_cookie(session_payload)

    return SimpleNamespace(cookies=cookies, state=SimpleNamespace())


def _authenticated_payload() -> dict:
    return {
        "accountState": "authenticated",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": None,
    }


def test_mounted_auth_login_route_shares_patchable_auth_service_module():
    import api.routes.auth as route_auth

    login_route = next(route for route in app.routes if getattr(route, "path", None) == "/api/auth/login")

    assert login_route.endpoint.__globals__["auth_service"] is route_auth.auth_service


@pytest.mark.asyncio
async def test_build_account_state_payload_returns_authenticated_shape():
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(
                return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": True}
            ),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"id": "tenant-1", "is_active": True})),
    ):
        payload = await build_account_state_payload(user_id="user-1", email="ops@example.com")

    assert payload == _authenticated_payload()


@pytest.mark.asyncio
async def test_build_account_state_payload_returns_not_provisioned_shape_for_existing_profile_with_null_tenant():
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": None, "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock()) as get_tenant_state,
    ):
        payload = await build_account_state_payload(user_id="user-1", email="ops@example.com")

    assert payload == {
        "accountState": "not_provisioned",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": None,
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": None,
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your account exists but is not assigned to a tenant yet.",
    }
    get_tenant_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_account_state_payload_returns_inactive_tenant_shape():
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(
                return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": True}
            ),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"id": "tenant-1", "is_active": False})),
    ):
        payload = await build_account_state_payload(user_id="user-1", email="ops@example.com")

    assert payload == {
        "accountState": "inactive_tenant",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your tenant access is inactive.",
    }


@pytest.mark.asyncio
async def test_build_account_state_payload_treats_missing_profile_active_state_as_inactive_user():
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": None}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock()) as get_tenant_state,
    ):
        payload = await build_account_state_payload(user_id="user-1", email="ops@example.com")

    assert payload == {
        "accountState": "inactive_user",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": None,
            "email": "ops@example.com",
        },
        "message": "Your account is inactive.",
    }
    get_tenant_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_account_state_payload_treats_missing_tenant_active_state_as_inactive_tenant():
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(
                return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": True}
            ),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"id": "tenant-1", "is_active": None})),
    ):
        payload = await build_account_state_payload(user_id="user-1", email="ops@example.com")

    assert payload == {
        "accountState": "inactive_tenant",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your tenant access is inactive.",
    }


@pytest.mark.asyncio
async def test_require_authenticated_rejects_missing_backend_session(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await require_authenticated(request=_request_with_session_cookie(monkeypatch))

    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "auth_authorization_header_missing",
        "message": "Authorization header required (Bearer token)",
    }


@pytest.mark.asyncio
async def test_require_authenticated_rejects_missing_profile(monkeypatch):
    with (
        patch("api.auth.get_user_profile", new=AsyncMock(return_value=None)),
        patch("api.auth.get_tenant_state", new=AsyncMock()) as get_tenant_state,
    ):
        with pytest.raises(HTTPException) as exc:
            await require_authenticated(
                request=_request_with_session_cookie(
                    monkeypatch,
                    _authenticated_session_payload(),
                )
            )

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "auth_profile_missing",
        "message": "User profile not found",
    }
    get_tenant_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_authenticated_rejects_null_tenant_assignment(monkeypatch):
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": None, "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock()) as get_tenant_state,
    ):
        with pytest.raises(HTTPException) as exc:
            await require_authenticated(
                request=_request_with_session_cookie(
                    monkeypatch,
                    _authenticated_session_payload(),
                )
            )

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "auth_tenant_unassigned",
        "message": "User is not assigned to a tenant",
    }
    get_tenant_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_authenticated_rejects_inactive_profile(monkeypatch):
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": False}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock()) as get_tenant_state,
    ):
        with pytest.raises(HTTPException) as exc:
            await require_authenticated(
                request=_request_with_session_cookie(
                    monkeypatch,
                    _authenticated_session_payload(),
                )
            )

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "auth_profile_inactive",
        "message": "User profile is inactive",
    }
    get_tenant_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_authenticated_rejects_inactive_tenant(monkeypatch):
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": False})),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_authenticated(
                request=_request_with_session_cookie(
                    monkeypatch,
                    _authenticated_session_payload(),
                )
            )

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "auth_tenant_inactive",
        "message": "Tenant is inactive",
    }


@pytest.mark.asyncio
async def test_require_authenticated_returns_auth_state_for_active_admin(monkeypatch):
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-1", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": True})),
    ):
        auth = await require_authenticated(
            request=_request_with_session_cookie(
                monkeypatch,
                _authenticated_session_payload(),
            )
        )

    assert auth == AuthContext(
        user_id="user-1",
        role="admin",
        tenant_id="tenant-1",
        profile_is_active=True,
        tenant_is_active=True,
    )


def test_api_client_uses_cookie_backed_auth_dependency_by_default(api_client, monkeypatch):
    monkeypatch.setattr(settings, "centrifugo_token_secret", "test-secret")

    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "default", "is_active": True}),
        ) as get_user_profile,
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": True})) as get_tenant_state,
    ):
        response = api_client.get("/api/v2/ws/token")

    assert response.status_code == 200
    assert "token" in response.json()
    get_user_profile.assert_awaited_once_with("u-test")
    get_tenant_state.assert_awaited_once_with("default")


def test_login_sets_backend_session_cookie_for_authenticated_account(api_client, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    with (
        patch(
            "api.routes.auth.auth_service.authenticate_password_login",
            new=AsyncMock(return_value={"user_id": "user-1", "email": "ops@example.com"}),
        ),
        patch(
            "api.routes.auth.auth_service.build_account_state_payload",
            new=AsyncMock(return_value=_authenticated_payload()),
        ),
    ):
        response = api_client.post(
            "/api/auth/login",
            json={"email": "ops@example.com", "password": "correct-horse-battery-staple"},
        )

    assert response.status_code == 200
    assert response.json() == _authenticated_payload()
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert response.cookies.get(settings.session_cookie_name)


def test_login_returns_not_provisioned_without_persisting_session(api_client, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    payload = {
        "accountState": "not_provisioned",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": None,
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": None,
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your account exists but is not assigned to a tenant yet.",
    }

    with (
        patch(
            "api.routes.auth.auth_service.authenticate_password_login",
            new=AsyncMock(return_value={"user_id": "user-1", "email": "ops@example.com"}),
        ),
        patch(
            "api.routes.auth.auth_service.build_account_state_payload",
            new=AsyncMock(return_value=payload),
        ),
    ):
        response = api_client.post(
            "/api/auth/login",
            json={"email": "ops@example.com", "password": "correct-horse-battery-staple"},
        )

    assert response.status_code == 403
    assert response.json() == payload
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.cookies.get(settings.session_cookie_name) is None


def test_login_returns_inactive_tenant_status_and_clears_session(api_client, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    payload = {
        "accountState": "inactive_tenant",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your tenant access is inactive.",
    }

    with (
        patch(
            "api.routes.auth.auth_service.authenticate_password_login",
            new=AsyncMock(return_value={"user_id": "user-1", "email": "ops@example.com"}),
        ),
        patch(
            "api.routes.auth.auth_service.build_account_state_payload",
            new=AsyncMock(return_value=payload),
        ),
    ):
        response = api_client.post(
            "/api/auth/login",
            json={"email": "ops@example.com", "password": "correct-horse-battery-staple"},
        )

    assert response.status_code == 403
    assert response.json() == payload
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.cookies.get(settings.session_cookie_name) is None


def test_me_reads_backend_session_cookie_and_returns_authenticated_payload(api_client, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    api_client.cookies.set(
        settings.session_cookie_name,
        build_session_cookie(
            {
                "user_id": "user-1",
                "email": "ops@example.com",
                "tenant_id": "tenant-1",
                "role": "admin",
                "account_state": "authenticated",
                "profile_is_active": True,
                "tenant_is_active": True,
            }
        ),
    )

    refreshed_payload = {
        "accountState": "authenticated",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-2",
        "role": "viewer",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-2",
            "role": "viewer",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": None,
    }

    with patch(
        "api.routes.auth.auth_service.build_account_state_payload",
        new=AsyncMock(return_value=refreshed_payload),
    ) as build_account_state_payload:
        response = api_client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == refreshed_payload
    build_account_state_payload.assert_awaited_once_with(
        user_id="user-1",
        email="ops@example.com",
    )


def test_me_returns_unauthenticated_payload_without_backend_session(api_client):
    api_client.cookies.clear()

    response = api_client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "accountState": "unauthenticated",
        "userId": None,
        "email": None,
        "tenantId": None,
        "role": None,
        "profile": None,
        "message": "Not authenticated",
    }


def test_me_revalidates_current_authority_and_clears_cookie_when_account_is_no_longer_authenticated(
    api_client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    api_client.cookies.set(
        settings.session_cookie_name,
        build_session_cookie(
            {
                "user_id": "user-1",
                "email": "ops@example.com",
                "tenant_id": "tenant-1",
                "role": "admin",
                "account_state": "authenticated",
                "profile_is_active": True,
                "tenant_is_active": True,
            }
        ),
    )

    inactive_payload = {
        "accountState": "inactive_tenant",
        "userId": "user-1",
        "email": "ops@example.com",
        "tenantId": "tenant-1",
        "role": "admin",
        "profile": {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "role": "admin",
            "is_active": True,
            "email": "ops@example.com",
        },
        "message": "Your tenant access is inactive.",
    }

    with patch(
        "api.routes.auth.auth_service.build_account_state_payload",
        new=AsyncMock(return_value=inactive_payload),
    ) as build_account_state_payload:
        response = api_client.get("/api/auth/me")

    assert response.status_code == 403
    assert response.json() == inactive_payload
    assert "Max-Age=0" in response.headers["set-cookie"]
    build_account_state_payload.assert_awaited_once_with(
        user_id="user-1",
        email="ops@example.com",
    )


def test_logout_clears_backend_session_cookie(api_client, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "session_secure_cookie", False)

    api_client.cookies.set(
        settings.session_cookie_name,
        build_session_cookie(
            {
                "user_id": "user-1",
                "email": "ops@example.com",
                "tenant_id": "tenant-1",
                "role": "admin",
                "account_state": "authenticated",
                "profile_is_active": True,
                "tenant_is_active": True,
            }
        ),
    )

    response = api_client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_login_invalid_credentials_preserves_canonical_auth_error_envelope(api_client):
    from api import auth as auth_service

    with patch(
        "api.routes.auth.auth_service.authenticate_password_login",
        new=AsyncMock(
            side_effect=auth_service.build_auth_error(
                status_code=401,
                code="auth_invalid_credentials",
                message="Invalid email or password",
            )
        ),
    ):
        response = api_client.post(
            "/api/auth/login",
            json={"email": "ops@example.com", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "auth_invalid_credentials",
            "message": "Invalid email or password",
        }
    }


def test_login_validation_failure_returns_canonical_error_envelope(api_client):
    response = api_client.post(
        "/api/auth/login",
        json={"email": "ops@example.com"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "validation_error",
            "message": "Request validation failed",
            "fields": [
                {
                    "field": "body.password",
                    "message": "Field required",
                }
            ],
        }
    }


def test_admin_only_route_denial_preserves_canonical_auth_error_envelope(api_client):
    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "viewer", "tenant_id": "default", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"id": "default", "is_active": True})),
    ):
        response = api_client.post(
            "/api/v2/cameras",
            json={
                "name": "Front Door",
                "rtsp_url": "rtsp://camera",
                "location": "Lobby",
                "zone": "Entrance",
                "is_entrance": True,
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "auth_insufficient_permissions",
            "message": "Admin access required",
        }
    }
