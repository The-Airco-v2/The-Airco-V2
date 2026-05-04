"""Contract tests for the Centrifugo WebSocket token route."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import jwt
import pytest

from airco.config import settings


def test_ws_token_uses_authenticated_principal_and_tenant_channel_claims(api_client, monkeypatch):
    monkeypatch.setattr(settings, "centrifugo_token_secret", "test-secret")

    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-42", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": True})),
        patch("api.routes.centrifugo_proxy.time.time", return_value=1_700_000_000),
    ):
        response = api_client.get("/api/v2/ws/token")

    assert response.status_code == 200

    claims = jwt.decode(
        response.json()["token"],
        "test-secret",
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert claims["sub"] == "u-test"
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_003_600
    assert claims["info"] == {
        "user_id": "u-test",
        "tenant_id": "tenant-42",
        "role": "admin",
    }
    assert claims["channels"] == ["tenant:tenant-42:overview"]


@pytest.mark.parametrize("secret_value", [None, "", "changeme", "  changeme  "])
def test_ws_token_rejects_missing_or_placeholder_signing_secret(api_client, monkeypatch, secret_value):
    monkeypatch.setattr(settings, "centrifugo_token_secret", secret_value)

    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "admin", "tenant_id": "tenant-42", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": True})),
    ):
        response = api_client.get("/api/v2/ws/token")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "auth_centrifugo_token_not_configured",
            "message": "Centrifugo token signing is not configured",
        }
    }


def test_ws_token_uses_profile_resolved_tenant_for_channel_authorization(api_client, monkeypatch):
    monkeypatch.setattr(settings, "centrifugo_token_secret", "test-secret")

    with (
        patch(
            "api.auth.get_user_profile",
            new=AsyncMock(return_value={"role": "viewer", "tenant_id": "tenant-99", "is_active": True}),
        ),
        patch("api.auth.get_tenant_state", new=AsyncMock(return_value={"is_active": True})),
        patch("api.routes.centrifugo_proxy.time.time", return_value=1_700_000_000),
    ):
        response = api_client.get("/api/v2/ws/token")

    assert response.status_code == 200

    claims = jwt.decode(
        response.json()["token"],
        "test-secret",
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert claims["sub"] == "u-test"
    assert claims["info"]["tenant_id"] == "tenant-99"
    assert claims["channels"] == ["tenant:tenant-99:overview"]
