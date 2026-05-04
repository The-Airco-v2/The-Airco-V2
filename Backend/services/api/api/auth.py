"""Supabase auth helpers, session cookies, and FastAPI dependencies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request, Response
from jwt.algorithms import ECAlgorithm

from airco.config import settings
from api.authority import AuthorityLookupError, fetch_profile_state, fetch_tenant_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JWKS cache — fetched once on first request, refreshed on key rotation
# ---------------------------------------------------------------------------

_jwks_cache: dict | None = None


def build_auth_error(*, status_code: int, code: str, message: str) -> HTTPException:
    """Return a stable auth error payload."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _session_secret_bytes(*, required: bool) -> bytes | None:
    secret = settings.session_secret or ""
    if secret:
        return secret.encode("utf-8")
    if required:
        raise build_auth_error(
            status_code=500,
            code="auth_session_not_configured",
            message="Session auth is not configured",
        )
    logger.error("[Auth] session_secret is not configured")
    return None


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def build_session_cookie(session: dict[str, Any]) -> str:
    """Build a signed backend session cookie value."""
    secret = _session_secret_bytes(required=True)
    now = int(time.time())
    payload = dict(session)
    payload.setdefault("iat", now)
    payload.setdefault("exp", now + max(1, settings.session_ttl_seconds))

    encoded_payload = _b64_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_b64_encode(signature)}"


def decode_session_cookie(cookie_value: str | None) -> dict[str, Any] | None:
    """Decode and verify a signed backend session cookie value."""
    if not cookie_value:
        return None

    secret = _session_secret_bytes(required=False)
    if secret is None:
        return None

    try:
        encoded_payload, encoded_signature = cookie_value.split(".", 1)
        expected_signature = hmac.new(
            secret,
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = _b64_decode(encoded_signature)
        if not hmac.compare_digest(expected_signature, actual_signature):
            return None

        payload = json.loads(_b64_decode(encoded_payload))
        if not isinstance(payload, dict):
            return None

        exp = payload.get("exp")
        if not isinstance(exp, int) or exp <= int(time.time()):
            return None

        return payload
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


def set_session_cookie(response: Response, session: dict[str, Any]) -> None:
    """Persist the signed backend session cookie on the response."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=build_session_cookie(session),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_secure_cookie,
        samesite=settings.session_same_site,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the backend session cookie."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_secure_cookie,
        samesite=settings.session_same_site,
        path="/",
    )


def read_session_cookie(request: Request) -> dict[str, Any] | None:
    """Read and decode the signed backend session cookie from the request."""
    return decode_session_cookie(request.cookies.get(settings.session_cookie_name))


def build_unauthenticated_payload(message: str = "Not authenticated") -> dict[str, Any]:
    return {
        "accountState": "unauthenticated",
        "userId": None,
        "email": None,
        "tenantId": None,
        "role": None,
        "profile": None,
        "message": message,
    }


def account_state_status_code(account_state: str) -> int:
    if account_state == "authenticated":
        return 200
    if account_state == "unauthenticated":
        return 401
    if account_state in {"not_provisioned", "inactive_user", "inactive_tenant"}:
        return 403
    return 500


def _message_for_account_state(account_state: str) -> str | None:
    if account_state == "authenticated":
        return None
    if account_state == "not_provisioned":
        return "Your account exists but is not assigned to a tenant yet."
    if account_state == "inactive_user":
        return "Your account is inactive."
    if account_state == "inactive_tenant":
        return "Your tenant access is inactive."
    if account_state == "error":
        return "Failed to fetch profile"
    return "Not authenticated"


def _is_active_flag(value: Any) -> bool:
    return value is True


def _validated_jwks(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise build_auth_error(
            status_code=502,
            code="auth_jwks_invalid",
            message="Could not verify token signing keys",
        )

    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(item, dict) for item in keys):
        raise build_auth_error(
            status_code=502,
            code="auth_jwks_invalid",
            message="Could not verify token signing keys",
        )

    return payload


def _profile_payload_from_authority(
    *,
    user_id: str,
    email: str | None,
    profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "id": user_id,
        "tenant_id": profile.get("tenant_id"),
        "role": profile.get("role"),
        "is_active": profile.get("is_active"),
        "email": email,
    }


def _profile_payload_from_session(session: dict[str, Any]) -> dict[str, Any] | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    return {
        "id": user_id,
        "tenant_id": session.get("tenant_id"),
        "role": session.get("role"),
        "is_active": session.get("profile_is_active"),
        "email": session.get("email"),
    }


def build_session_payload_from_account_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an authenticated account-state payload into a session cookie payload."""
    if payload.get("accountState") != "authenticated":
        return None

    return {
        "user_id": payload.get("userId"),
        "email": payload.get("email"),
        "tenant_id": payload.get("tenantId"),
        "role": payload.get("role"),
        "account_state": payload.get("accountState"),
        "profile_is_active": payload.get("profile", {}).get("is_active"),
        "tenant_is_active": True,
    }


def build_account_payload_from_session(session: dict[str, Any] | None) -> dict[str, Any]:
    """Convert decoded session state into the stable auth payload shape."""
    if not session:
        return build_unauthenticated_payload()

    account_state = session.get("account_state")
    user_id = session.get("user_id")
    if not isinstance(account_state, str) or not isinstance(user_id, str):
        return build_unauthenticated_payload()

    return {
        "accountState": account_state,
        "userId": user_id,
        "email": session.get("email"),
        "tenantId": session.get("tenant_id"),
        "role": session.get("role"),
        "profile": _profile_payload_from_session(session),
        "message": _message_for_account_state(account_state),
    }


async def _fetch_jwks() -> dict:
    """Fetch Supabase JWKS and return the parsed JSON."""
    url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            return _validated_jwks(resp.json())
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.error("[Auth] JWKS fetch failed: %s", exc)
        raise build_auth_error(
            status_code=502,
            code="auth_jwks_fetch_failed",
            message="Could not verify token signing keys",
        )
    except (TypeError, ValueError) as exc:
        logger.error("[Auth] JWKS payload invalid: %s", exc)
        raise build_auth_error(
            status_code=502,
            code="auth_jwks_invalid",
            message="Could not verify token signing keys",
        )


def _token_kid(token: str) -> str:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_token",
            message="Invalid token",
        )

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_token",
            message="Invalid token",
        )
    return kid


def _select_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key_data in jwks.get("keys", []):
        if key_data.get("kid") == kid:
            return key_data
    return None


async def _get_public_key(token: str):
    """Return the cached ECC public key for the JWT header kid."""
    global _jwks_cache
    kid = _token_kid(token)
    if _jwks_cache is None:
        _jwks_cache = await _fetch_jwks()

    key_data = _select_jwk(_jwks_cache, kid)
    if key_data is None:
        _jwks_cache = await _fetch_jwks()
        key_data = _select_jwk(_jwks_cache, kid)
    if key_data is None:
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_token",
            message="Invalid token",
        )

    try:
        return ECAlgorithm.from_jwk(json.dumps(key_data))
    except Exception as exc:
        logger.error("[Auth] JWKS key parse failed for kid=%s: %s", kid, exc)
        raise build_auth_error(
            status_code=502,
            code="auth_jwks_invalid",
            message="Could not verify token signing keys",
        )


async def authenticate_password_login(email: str, password: str) -> dict[str, str | None]:
    """Authenticate credentials against Supabase password auth."""
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise build_auth_error(
            status_code=500,
            code="auth_not_configured",
            message="Supabase auth is not configured",
        )

    url = f"{settings.supabase_url}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"email": email, "password": password},
            )
    except httpx.HTTPError as exc:
        logger.error("[Auth] Supabase login request failed: %s", exc)
        raise build_auth_error(
            status_code=502,
            code="auth_login_unavailable",
            message="Authentication service unavailable",
        )

    if response.status_code in (400, 401):
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_credentials",
            message="Invalid email or password",
        )

    if response.status_code == 429:
        logger.warning("[Auth] Supabase login rate limited: %s", response.text)
        raise build_auth_error(
            status_code=429,
            code="auth_login_rate_limited",
            message="Too many login attempts. Please try again later.",
        )

    if response.status_code >= 500:
        logger.error("[Auth] Supabase login upstream failure: %s", response.text)
        raise build_auth_error(
            status_code=502,
            code="auth_login_unavailable",
            message="Authentication service unavailable",
        )

    if response.status_code != 200:
        logger.warning("[Auth] Supabase login rejected: %s", response.text)
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_credentials",
            message="Invalid email or password",
        )

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        logger.error("[Auth] Supabase login response was not valid JSON: %s", exc)
        raise build_auth_error(
            status_code=502,
            code="auth_invalid_upstream_response",
            message="Authentication service returned an invalid response",
        )

    user = payload.get("user") if isinstance(payload, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    user_email = user.get("email") if isinstance(user, dict) else None
    if not isinstance(user_id, str) or not user_id:
        logger.error("[Auth] Supabase login response missing user id: %s", payload)
        raise build_auth_error(
            status_code=502,
            code="auth_invalid_upstream_response",
            message="Authentication service returned an invalid response",
        )

    return {"user_id": user_id, "email": user_email}


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------


async def verify_jwt(token: str) -> dict:
    """
    Verify a Supabase-issued JWT using the JWKS public key (ES256).

    Returns the decoded claims dict on success.
    Raises HTTPException(401) on failure.
    Automatically refreshes the JWKS cache once if verification fails
    (handles key rotation).
    """
    global _jwks_cache

    async def _decode(key) -> dict:
        return jwt.decode(
            token,
            key,
            algorithms=["ES256"],
            audience="authenticated",
            options={"verify_exp": True},
        )

    try:
        public_key = await _get_public_key(token)
        return await _decode(public_key)
    except HTTPException:
        raise
    except jwt.InvalidSignatureError:
        logger.info("[Auth] JWT signature invalid, refreshing JWKS cache")
        _jwks_cache = None
        try:
            public_key = await _get_public_key(token)
            return await _decode(public_key)
        except jwt.ExpiredSignatureError:
            raise build_auth_error(
                status_code=401,
                code="auth_token_expired",
                message="Token expired",
            )
        except jwt.PyJWTError as exc:
            logger.warning("[Auth] JWT verification failed after cache refresh: %s", exc)
            raise build_auth_error(
                status_code=401,
                code="auth_invalid_token",
                message="Invalid token",
            )
    except jwt.ExpiredSignatureError:
        raise build_auth_error(
            status_code=401,
            code="auth_token_expired",
            message="Token expired",
        )
    except jwt.PyJWTError as exc:
        logger.warning("[Auth] JWT verification failed: %s", exc)
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_token",
            message="Invalid token",
        )


# ---------------------------------------------------------------------------
# User profile lookup
# ---------------------------------------------------------------------------


async def get_user_profile(user_id: str) -> dict | None:
    """Fetch role, tenant assignment, and active state via the authority seam."""
    try:
        profile = await fetch_profile_state(user_id)
    except AuthorityLookupError as exc:
        logger.error("[Auth] user_profiles lookup failed: %s", exc.code)
        raise build_auth_error(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    if profile is None:
        logger.warning("[Auth] No user_profile found for user_id=%s", user_id)
        return None

    return profile


async def get_tenant_state(tenant_id: str) -> dict | None:
    """Fetch the tenant active state via the authority seam."""
    try:
        tenant = await fetch_tenant_state(tenant_id)
    except AuthorityLookupError as exc:
        logger.error("[Auth] tenants lookup failed: %s", exc.code)
        raise build_auth_error(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    if tenant is None:
        logger.warning("[Auth] No tenant found for tenant_id=%s", tenant_id)
        return None

    return tenant


async def build_account_state_payload(*, user_id: str, email: str | None) -> dict[str, Any]:
    """Resolve profile and tenant authority into a stable account-state payload."""
    profile = await get_user_profile(user_id)
    profile_payload = _profile_payload_from_authority(user_id=user_id, email=email, profile=profile)

    if profile is None or profile.get("tenant_id") is None:
        return {
            "accountState": "not_provisioned",
            "userId": user_id,
            "email": email,
            "tenantId": profile.get("tenant_id") if profile else None,
            "role": profile.get("role") if profile else None,
            "profile": profile_payload,
            "message": "Your account exists but is not assigned to a tenant yet.",
        }

    if not _is_active_flag(profile.get("is_active")):
        return {
            "accountState": "inactive_user",
            "userId": user_id,
            "email": email,
            "tenantId": profile.get("tenant_id"),
            "role": profile.get("role"),
            "profile": profile_payload,
            "message": "Your account is inactive.",
        }

    tenant_id = str(profile["tenant_id"])
    tenant = await get_tenant_state(tenant_id)
    if tenant is None:
        return {
            "accountState": "not_provisioned",
            "userId": user_id,
            "email": email,
            "tenantId": tenant_id,
            "role": profile.get("role"),
            "profile": profile_payload,
            "message": "Your account is assigned to a tenant that is not available yet.",
        }

    if not _is_active_flag(tenant.get("is_active")):
        return {
            "accountState": "inactive_tenant",
            "userId": user_id,
            "email": email,
            "tenantId": tenant_id,
            "role": profile.get("role"),
            "profile": profile_payload,
            "message": "Your tenant access is inactive.",
        }

    return {
        "accountState": "authenticated",
        "userId": user_id,
        "email": email,
        "tenantId": tenant_id,
        "role": profile.get("role"),
        "profile": profile_payload,
        "message": None,
    }


# ---------------------------------------------------------------------------
# Auth state
# ---------------------------------------------------------------------------


@dataclass
class AuthContext:
    user_id: str
    role: str
    tenant_id: str
    profile_is_active: bool = True
    tenant_is_active: bool = True


AuthState = AuthContext


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def _get_request_state_auth_context(request: Request) -> AuthContext | None:
    state = getattr(request, "state", None)
    cached = getattr(state, "auth_context", None) if state is not None else None
    if isinstance(cached, AuthContext):
        return cached
    return None


def _set_request_state_auth_context(request: Request, auth: AuthContext) -> None:
    state = getattr(request, "state", None)
    if state is not None:
        setattr(state, "auth_context", auth)


def _session_from_request(request: Request) -> dict[str, Any] | None:
    state = getattr(request, "state", None)
    cached_session = getattr(state, "auth_session", None) if state is not None else None
    if isinstance(cached_session, dict):
        return cached_session

    session = read_session_cookie(request)
    if state is not None and isinstance(session, dict):
        setattr(state, "auth_session", session)
    return session


async def _resolve_auth_context(*, user_id: str) -> AuthContext:
    profile = await get_user_profile(user_id)
    if profile is None:
        raise build_auth_error(
            status_code=403,
            code="auth_profile_missing",
            message="User profile not found",
        )

    tenant_id = profile.get("tenant_id")
    if tenant_id is None:
        raise build_auth_error(
            status_code=403,
            code="auth_tenant_unassigned",
            message="User is not assigned to a tenant",
        )
    tenant_id = str(tenant_id)

    profile_is_active = _is_active_flag(profile.get("is_active"))
    if not profile_is_active:
        raise build_auth_error(
            status_code=403,
            code="auth_profile_inactive",
            message="User profile is inactive",
        )

    tenant = await get_tenant_state(tenant_id)
    if tenant is None:
        raise build_auth_error(
            status_code=403,
            code="auth_tenant_missing",
            message="Tenant not found",
        )

    tenant_is_active = _is_active_flag(tenant.get("is_active"))
    if not tenant_is_active:
        raise build_auth_error(
            status_code=403,
            code="auth_tenant_inactive",
            message="Tenant is inactive",
        )

    return AuthContext(
        user_id=user_id,
        role=profile["role"],
        tenant_id=tenant_id,
        profile_is_active=profile_is_active,
        tenant_is_active=tenant_is_active,
    )


async def require_authenticated(
    request: Request = None,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """
    Require an authenticated backend session for route access.
    Resolves current authority live from Supabase for role and tenant checks.
    """
    if request is not None:
        cached = _get_request_state_auth_context(request)
        if cached is not None:
            return cached

        session = _session_from_request(request)
        if session is None:
            raise build_auth_error(
                status_code=401,
                code="auth_authorization_header_missing",
                message="Authorization header required (Bearer token)",
            )

        user_id = session.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            raise build_auth_error(
                status_code=401,
                code="auth_authorization_header_missing",
                message="Authorization header required (Bearer token)",
            )

        auth = await _resolve_auth_context(user_id=user_id)
        _set_request_state_auth_context(request, auth)
        return auth

    if not authorization or not authorization.startswith("Bearer "):
        raise build_auth_error(
            status_code=401,
            code="auth_authorization_header_missing",
            message="Authorization header required (Bearer token)",
        )

    token = authorization[len("Bearer "):]
    claims = await verify_jwt(token)
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise build_auth_error(
            status_code=401,
            code="auth_invalid_token_claims",
            message="Invalid token claims",
        )
    return await _resolve_auth_context(user_id=user_id)


async def require_admin(
    auth: AuthContext = Depends(require_authenticated),
) -> AuthContext:
    """
    Require role admin.
    Raises HTTP 403 for viewers.
    """
    if auth.role != "admin":
        raise build_auth_error(
            status_code=403,
            code="auth_insufficient_permissions",
            message="Admin access required",
        )
    return auth
