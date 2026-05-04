"""Shared pytest setup for v2 tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

V2_ROOT = Path(__file__).resolve().parent.parent
SHARED_ROOT = V2_ROOT / "shared"
SERVICE_PACKAGE_ROOTS = (
    SHARED_ROOT,
    V2_ROOT / "services" / "api",
    V2_ROOT / "services" / "analytics-consumer",
    V2_ROOT / "services" / "identity-consumer",
    V2_ROOT / "services" / "snapshot-consumer",
    V2_ROOT / "services" / "ws-publisher",
)


for package_root in SERVICE_PACKAGE_ROOTS:
    package_root_str = str(package_root)
    if package_root_str not in sys.path:
        sys.path.insert(0, package_root_str)

from airco.config import settings


class _DummyAsyncSessionMaker:
    async def __aenter__(self):
        return AsyncMock()

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __call__(self):
        return self


def _load_api_test_targets():
    from unittest.mock import patch

    import sqlalchemy.ext.asyncio as sa_asyncio
    from sqlalchemy.types import UserDefinedType

    pgvector_module = ModuleType("pgvector")
    pgvector_sqlalchemy_module = ModuleType("pgvector.sqlalchemy")

    class Vector(UserDefinedType):
        def __init__(self, *args, **kwargs):
            pass

        def get_col_spec(self, **kw):
            return "VECTOR"

    pgvector_sqlalchemy_module.Vector = Vector
    pgvector_module.sqlalchemy = pgvector_sqlalchemy_module

    sys.modules.setdefault("pgvector", pgvector_module)
    sys.modules.setdefault("pgvector.sqlalchemy", pgvector_sqlalchemy_module)

    with patch.object(sa_asyncio, "create_async_engine", return_value=object()), patch.object(
        sa_asyncio, "async_sessionmaker", return_value=_DummyAsyncSessionMaker()
    ):
        from api.auth import AuthContext, build_session_cookie, require_authenticated
        from api.main import app
        from airco.db import get_session

    return AuthContext, build_session_cookie, require_authenticated, app, get_session


AuthContext, build_session_cookie, require_authenticated, app, get_session = _load_api_test_targets()


@pytest.fixture
def auth_state():
    return None


@pytest.fixture
def auth_session_payload():
    return {
        "user_id": "u-test",
        "email": "ops@example.com",
        "tenant_id": "default",
        "role": "admin",
        "account_state": "authenticated",
        "profile_is_active": True,
        "tenant_is_active": True,
    }


@pytest.fixture
def auth_profile_state(auth_session_payload):
    if auth_session_payload is None:
        return None

    return {
        "role": auth_session_payload.get("role"),
        "tenant_id": auth_session_payload.get("tenant_id"),
        "is_active": auth_session_payload.get("profile_is_active"),
    }


@pytest.fixture
def auth_tenant_state(auth_session_payload):
    if auth_session_payload is None:
        return None

    return {
        "id": auth_session_payload.get("tenant_id"),
        "is_active": auth_session_payload.get("tenant_is_active"),
    }


@pytest.fixture
def db_session_mock():
    return AsyncMock()


@pytest.fixture
def api_client(
    auth_state,
    auth_session_payload,
    auth_profile_state,
    auth_tenant_state,
    db_session_mock,
    monkeypatch,
):
    previous_overrides = {
        require_authenticated: app.dependency_overrides.get(require_authenticated),
        get_session: app.dependency_overrides.get(get_session),
    }

    if auth_state is not None:
        app.dependency_overrides[require_authenticated] = lambda: auth_state
    else:
        app.dependency_overrides.pop(require_authenticated, None)

    async def override_session():
        yield db_session_mock

    app.dependency_overrides[get_session] = override_session
    try:
        if auth_state is None:
            with (
                patch("api.auth.get_user_profile", new=AsyncMock(return_value=auth_profile_state)),
                patch("api.auth.get_tenant_state", new=AsyncMock(return_value=auth_tenant_state)),
            ):
                with TestClient(app) as client:
                    if auth_session_payload is not None:
                        monkeypatch.setattr(settings, "session_secret", "test-session-secret")
                        client.cookies.set(
                            settings.session_cookie_name,
                            build_session_cookie(auth_session_payload),
                        )
                    yield client
        else:
            with TestClient(app) as client:
                yield client
    finally:
        for dependency, previous_override in previous_overrides.items():
            if previous_override is None:
                app.dependency_overrides.pop(dependency, None)
            else:
                app.dependency_overrides[dependency] = previous_override
