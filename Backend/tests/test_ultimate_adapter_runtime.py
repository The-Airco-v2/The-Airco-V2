from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from ultimate_adapter.runtime import UltimateSessionRuntime  # noqa: E402
import ultimate_adapter.service_runtime as service_runtime  # noqa: E402
from ultimate_adapter.config import UltimateAdapterSettings  # noqa: E402
from ultimate_adapter.service_runtime import _is_retryable_frame_error  # noqa: E402


class _FakeFacade:
    def __init__(self, frame_result, cleanup_result, shutdown_result):
        self.frame_result = frame_result
        self.cleanup_result = cleanup_result
        self.shutdown_result = shutdown_result
        self.processed = []

    def process_frame(self, frame, detections=None):
        self.processed.append((frame, detections))
        return self.frame_result

    def cleanup(self):
        return self.cleanup_result

    def shutdown(self):
        return self.shutdown_result


class _FakeOutputAdapter:
    def __init__(self):
        self.frame_results = []
        self.cleanup_results = []

    async def publish_frame_result(self, db, *, result, frame=None, observed_at=None):
        self.frame_results.append((db, result, frame, observed_at))

    async def publish_cleanup(self, db, *, result, observed_at=None):
        self.cleanup_results.append((db, result, observed_at))


@pytest.mark.asyncio
async def test_runtime_process_frame_routes_core_result_into_output_adapter():
    observed_at = datetime(2026, 4, 19, 13, 0, tzinfo=timezone.utc)
    frame_result = object()
    output_adapter = _FakeOutputAdapter()
    runtime = UltimateSessionRuntime(
        facade=_FakeFacade(frame_result, object(), object()),
        output_adapter=output_adapter,
    )
    db = object()

    result = await runtime.process_frame(
        db,
        frame="frame-bytes",
        detections="detections",
        observed_at=observed_at,
    )

    assert result is frame_result
    assert output_adapter.frame_results == [(db, frame_result, "frame-bytes", observed_at)]


@pytest.mark.asyncio
async def test_runtime_cleanup_and_shutdown_publish_cleanup_results():
    observed_at = datetime(2026, 4, 19, 13, 5, tzinfo=timezone.utc)
    cleanup_result = object()
    shutdown_result = object()
    output_adapter = _FakeOutputAdapter()
    runtime = UltimateSessionRuntime(
        facade=_FakeFacade(object(), cleanup_result, shutdown_result),
        output_adapter=output_adapter,
    )
    db = object()

    cleanup = await runtime.cleanup(db, observed_at=observed_at)
    shutdown = await runtime.shutdown(db, observed_at=observed_at)

    assert cleanup is cleanup_result
    assert shutdown is shutdown_result
    assert output_adapter.cleanup_results == [
        (db, cleanup_result, observed_at),
        (db, shutdown_result, observed_at),
    ]


def test_retryable_frame_error_detects_deadlock_messages():
    class _DeadlockError(Exception):
        pass

    err = _DeadlockError("deadlock detected")

    assert _is_retryable_frame_error(err) is True


def test_retryable_frame_error_rejects_unrelated_exceptions():
    assert _is_retryable_frame_error(RuntimeError("frame_processing_failed")) is False


def test_camera_workers_share_session_registry_and_isolate_other_sessions(monkeypatch, tmp_path):
    created_storage_dirs: list[str | None] = []

    class _FakeRegistry:
        def __init__(self, cfg):
            created_storage_dirs.append(cfg.get("embedding_storage_dir"))

        def close(self):
            return None

    class _FakeFacade:
        def __init__(self, *, session_id, camera_id, topology=None, config=None, detector=None, feature_extractor=None, registry=None):
            self.session_id = session_id
            self.camera_id = camera_id
            self.topology = topology or {}
            self.config = config
            self.bundle = SimpleNamespace(registry=registry)

    class _FakeRuntime:
        def __init__(self, *, facade, output_adapter):
            self.facade = facade
            self.output_adapter = output_adapter

    class _FakeOutputAdapter:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(service_runtime, "GlobalIdentityRegistry", _FakeRegistry)
    monkeypatch.setattr(service_runtime, "UltimateAdapterCoreFacade", _FakeFacade)
    monkeypatch.setattr(service_runtime, "UltimateSessionRuntime", _FakeRuntime)
    monkeypatch.setattr(service_runtime, "UltimateOutputAdapter", _FakeOutputAdapter)
    monkeypatch.setattr(service_runtime, "_build_shared_runtime_resources", lambda settings: ("detector", "extractor"))

    settings = UltimateAdapterSettings(embedding_storage_dir=str(tmp_path))
    worker_a = service_runtime.UltimateCameraWorker(
        "session-a",
        "camera-1",
        rtsp_url="rtsp://example/1",
        redis_client=object(),
        settings=settings,
    )
    worker_b = service_runtime.UltimateCameraWorker(
        "session-a",
        "camera-2",
        rtsp_url="rtsp://example/2",
        redis_client=object(),
        settings=settings,
    )
    worker_c = service_runtime.UltimateCameraWorker(
        "session-b",
        "camera-1",
        rtsp_url="rtsp://example/3",
        redis_client=object(),
        settings=settings,
    )

    assert worker_a._shared_registry is worker_b._shared_registry
    assert worker_a._runtime.facade.bundle.registry is worker_b._runtime.facade.bundle.registry
    assert worker_c._shared_registry is not worker_a._shared_registry
    assert created_storage_dirs == [
        str(tmp_path / "session-session-a"),
        str(tmp_path / "session-session-b"),
    ]

    service_runtime._release_shared_session_registry("session-a", worker_a._shared_registry)
    service_runtime._release_shared_session_registry("session-a", worker_b._shared_registry)
    service_runtime._release_shared_session_registry("session-b", worker_c._shared_registry)
