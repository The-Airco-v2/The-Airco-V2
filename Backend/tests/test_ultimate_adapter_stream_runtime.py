from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from ultimate_adapter.stream_runtime import (  # noqa: E402
    ACTIVE_SELECTOR_KEY,
    ACTIVE_SESSION_KEY,
    SESSION_ALIAS_CONTRACT_KEY,
    ULTIMATE_SELECTOR,
    UltimateRuntimeManager,
    read_active_runtime_context,
)


class _FakeRedis:
    def __init__(self, values: dict[str, str | None]):
        self.values = values

    async def mget(self, *keys: str):
        return [self.values.get(key) for key in keys]


@pytest.mark.asyncio
async def test_read_active_runtime_context_returns_none_for_non_ultimate_selector():
    redis = _FakeRedis(
        {
            ACTIVE_SESSION_KEY: "session-1",
            ACTIVE_SELECTOR_KEY: "standard",
            SESSION_ALIAS_CONTRACT_KEY: json.dumps(
                [{"session_id": "session-1", "camera_id": "cam-1", "rtsp_url": "rtsp://go2rtc/session_1"}]
            ),
        }
    )

    context = await read_active_runtime_context(redis)

    assert context is None


@pytest.mark.asyncio
async def test_read_active_runtime_context_parses_alias_contracts_for_ultimate_session():
    redis = _FakeRedis(
        {
            ACTIVE_SESSION_KEY: "session-1",
            ACTIVE_SELECTOR_KEY: ULTIMATE_SELECTOR,
            SESSION_ALIAS_CONTRACT_KEY: json.dumps(
                [
                    {"session_id": "session-1", "camera_id": "cam-1", "rtsp_url": "rtsp://go2rtc/session_1_cam_1"},
                    {"session_id": "session-1", "camera_id": "cam-2", "rtsp_url": "rtsp://go2rtc/session_1_cam_2"},
                ]
            ),
        }
    )

    context = await read_active_runtime_context(redis)

    assert context is not None
    assert context.session_id == "session-1"
    assert [contract.camera_id for contract in context.contracts] == ["cam-1", "cam-2"]


class _FakeWorker:
    def __init__(self, session_id: str, camera_id: str, rtsp_url: str):
        self.session_id = session_id
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.started = 0
        self.stopped = 0

    @property
    def worker_key(self) -> tuple[str, str]:
        return (self.session_id, self.camera_id)

    def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


@pytest.mark.asyncio
async def test_runtime_manager_starts_workers_for_active_ultimate_contracts():
    created: list[_FakeWorker] = []

    def worker_factory(*, session_id: str, camera_id: str, rtsp_url: str):
        worker = _FakeWorker(session_id, camera_id, rtsp_url)
        created.append(worker)
        return worker

    manager = UltimateRuntimeManager(worker_factory=worker_factory)

    await manager.apply_context(
        session_id="session-1",
        contracts=[
            {"session_id": "session-1", "camera_id": "cam-1", "rtsp_url": "rtsp://go2rtc/session_1_cam_1"},
            {"session_id": "session-1", "camera_id": "cam-2", "rtsp_url": "rtsp://go2rtc/session_1_cam_2"},
        ],
    )

    assert sorted(manager.workers) == [("session-1", "cam-1"), ("session-1", "cam-2")]
    assert [worker.started for worker in created] == [1, 1]


@pytest.mark.asyncio
async def test_runtime_manager_replaces_workers_when_session_changes():
    created: list[_FakeWorker] = []

    def worker_factory(*, session_id: str, camera_id: str, rtsp_url: str):
        worker = _FakeWorker(session_id, camera_id, rtsp_url)
        created.append(worker)
        return worker

    manager = UltimateRuntimeManager(worker_factory=worker_factory)
    await manager.apply_context(
        session_id="session-1",
        contracts=[{"session_id": "session-1", "camera_id": "cam-1", "rtsp_url": "rtsp://go2rtc/session_1_cam_1"}],
    )
    first = created[0]

    await manager.apply_context(
        session_id="session-2",
        contracts=[{"session_id": "session-2", "camera_id": "cam-9", "rtsp_url": "rtsp://go2rtc/session_2_cam_9"}],
    )

    assert first.stopped == 1
    assert sorted(manager.workers) == [("session-2", "cam-9")]


@pytest.mark.asyncio
async def test_runtime_manager_stop_all_cleans_up_existing_workers():
    created: list[_FakeWorker] = []

    def worker_factory(*, session_id: str, camera_id: str, rtsp_url: str):
        worker = _FakeWorker(session_id, camera_id, rtsp_url)
        created.append(worker)
        return worker

    manager = UltimateRuntimeManager(worker_factory=worker_factory)
    await manager.apply_context(
        session_id="session-1",
        contracts=[{"session_id": "session-1", "camera_id": "cam-1", "rtsp_url": "rtsp://go2rtc/session_1_cam_1"}],
    )

    await manager.stop_all()

    assert created[0].stopped == 1
    assert manager.workers == {}
