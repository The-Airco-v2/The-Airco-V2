"""Generate Centrifugo connection tokens for frontend WebSocket auth."""

import time
import jwt
from fastapi import APIRouter, Depends
from airco.config import settings
from api.auth import AuthState, build_auth_error, require_authenticated

router = APIRouter()

TOKEN_TTL_SECONDS = 3600
_UNSAFE_TOKEN_SECRETS = {"", "changeme"}


def _tenant_channels(auth: AuthState) -> list[str]:
    return [f"tenant:{auth.tenant_id}:overview"]


def _token_payload(auth: AuthState) -> dict[str, object]:
    issued_at = int(time.time())
    return {
        "sub": auth.user_id,
        "iat": issued_at,
        "exp": issued_at + TOKEN_TTL_SECONDS,
        "info": {
            "user_id": auth.user_id,
            "tenant_id": auth.tenant_id,
            "role": auth.role,
        },
        "channels": _tenant_channels(auth),
    }


def _centrifugo_token_secret() -> str:
    secret = settings.centrifugo_token_secret
    if not isinstance(secret, str):
        raise build_auth_error(
            status_code=500,
            code="auth_centrifugo_token_not_configured",
            message="Centrifugo token signing is not configured",
        )

    normalized = secret.strip().lower()
    if normalized in _UNSAFE_TOKEN_SECRETS:
        raise build_auth_error(
            status_code=500,
            code="auth_centrifugo_token_not_configured",
            message="Centrifugo token signing is not configured",
        )

    return secret


@router.get("/token")
async def get_ws_token(auth: AuthState = Depends(require_authenticated)):
    payload = _token_payload(auth)
    token = jwt.encode(payload, _centrifugo_token_secret(), algorithm="HS256")
    return {"token": token}
