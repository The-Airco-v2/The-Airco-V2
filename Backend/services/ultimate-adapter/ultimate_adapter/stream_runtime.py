"""Session-scoped go2rtc alias helpers and adapter runtime state management."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Awaitable, Callable

from ultimate_adapter.config import (
    ACTIVE_SELECTOR_KEY,
    ACTIVE_SESSION_KEY,
    SESSION_ALIAS_CONTRACT_KEY,
    ULTIMATE_SELECTOR,
)


def sanitize_stream_part(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


def session_camera_stream_name(session_id: object, camera_id: object) -> str:
    return f"session_{sanitize_stream_part(session_id)}_{sanitize_stream_part(camera_id)}"


def session_camera_stream_url(base_url: str, session_id: object, camera_id: object) -> str:
    return f"{base_url.rstrip('/')}/{session_camera_stream_name(session_id, camera_id)}"


@dataclass(frozen=True)
class SessionAliasContract:
    session_id: str
    camera_id: str
    stream_name: str
    rtsp_url: str


def build_session_alias_contract(
    *,
    base_url: str,
    session_id: object,
    camera_id: object,
) -> SessionAliasContract:
    """Describe the session-scoped stream alias the adapter should consume."""

    return SessionAliasContract(
        session_id=str(session_id),
        camera_id=str(camera_id),
        stream_name=session_camera_stream_name(session_id, camera_id),
        rtsp_url=session_camera_stream_url(base_url, session_id, camera_id),
    )


def session_alias_contract_payload(
    *,
    base_url: str,
    session_id: object,
    camera_id: object,
) -> dict[str, str]:
    return asdict(
        build_session_alias_contract(
            base_url=base_url,
            session_id=session_id,
            camera_id=camera_id,
        )
    )


@dataclass(frozen=True)
class ActiveRuntimeContext:
    session_id: str
    selector: str
    contracts: tuple[SessionAliasContract, ...]


def _parse_contracts(raw_value: Any) -> tuple[SessionAliasContract, ...]:
    if not raw_value:
        return ()
    payload = raw_value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return ()
    if not isinstance(payload, list):
        return ()

    contracts: list[SessionAliasContract] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        session_id = item.get("session_id")
        camera_id = item.get("camera_id")
        rtsp_url = item.get("rtsp_url")
        stream_name = item.get("stream_name") or session_camera_stream_name(session_id, camera_id)
        if session_id and camera_id and rtsp_url:
            contracts.append(
                SessionAliasContract(
                    session_id=str(session_id),
                    camera_id=str(camera_id),
                    stream_name=str(stream_name),
                    rtsp_url=str(rtsp_url),
                )
            )
    return tuple(contracts)


async def read_active_runtime_context(redis_client) -> ActiveRuntimeContext | None:
    active_session_id, active_selector, raw_contracts = await redis_client.mget(
        ACTIVE_SESSION_KEY,
        ACTIVE_SELECTOR_KEY,
        SESSION_ALIAS_CONTRACT_KEY,
    )
    if not active_session_id or str(active_selector) != ULTIMATE_SELECTOR:
        return None

    contracts = tuple(
        contract
        for contract in _parse_contracts(raw_contracts)
        if contract.session_id == str(active_session_id)
    )
    if not contracts:
        return None
    return ActiveRuntimeContext(
        session_id=str(active_session_id),
        selector=str(active_selector),
        contracts=contracts,
    )


class UltimateRuntimeManager:
    """Maintain one worker per active session-camera alias contract."""

    def __init__(self, *, worker_factory: Callable[..., Any]):
        self.worker_factory = worker_factory
        self.workers: dict[tuple[str, str], Any] = {}

    async def apply_context(self, *, session_id: str, contracts: list[dict[str, str]] | tuple[SessionAliasContract, ...]) -> None:
        desired_contracts = [
            contract if isinstance(contract, SessionAliasContract) else SessionAliasContract(
                session_id=str(contract["session_id"]),
                camera_id=str(contract["camera_id"]),
                stream_name=str(contract.get("stream_name") or session_camera_stream_name(contract["session_id"], contract["camera_id"])),
                rtsp_url=str(contract["rtsp_url"]),
            )
            for contract in contracts
        ]
        desired_keys = {(str(session_id), contract.camera_id) for contract in desired_contracts}
        stale_keys = set(self.workers) - desired_keys
        for key in stale_keys:
            worker = self.workers.pop(key, None)
            if worker is not None:
                await worker.stop()

        for contract in desired_contracts:
            key = (str(session_id), contract.camera_id)
            if key in self.workers:
                continue
            worker = self.worker_factory(
                session_id=str(session_id),
                camera_id=contract.camera_id,
                rtsp_url=contract.rtsp_url,
            )
            self.workers[key] = worker
            worker.start()

    async def stop_all(self) -> None:
        for key, worker in list(self.workers.items()):
            await worker.stop()
            self.workers.pop(key, None)
