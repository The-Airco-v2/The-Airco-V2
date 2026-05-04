"""Live runtime that pulls frames from go2rtc aliases and runs the Ultimate adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sys
from threading import Lock
import time
from datetime import datetime, timezone

from sqlalchemy.exc import DBAPIError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.db import async_session
from airco.redis_streams import get_redis
from ultimate_adapter.config import UltimateAdapterSettings, load_settings
from ultimate_adapter.output_adapter import UltimateOutputAdapter
from ultimate_adapter.runtime import UltimateSessionRuntime
from ultimate_adapter.stream_runtime import UltimateRuntimeManager, read_active_runtime_context
from ultimate_adapter.ultimate_core import UltimateAdapterCoreFacade, UltimateCoreConfig
from ultimate_adapter.ultimate_core.features import MultiScalePyramidExtractor, RobustFeatureExtractor
from ultimate_adapter.ultimate_core.registry import GlobalIdentityRegistry
from ultimate_adapter.ultimate_core.tracker import load_detector_model, resolve_device

logger = logging.getLogger("ultimate-adapter")

_SHARED_RUNTIME_RESOURCES: dict[tuple[str, str, str, bool], tuple[object, object]] = {}
_SHARED_RUNTIME_RESOURCES_LOCK = Lock()
_SHARED_SESSION_REGISTRIES: dict[str, tuple[GlobalIdentityRegistry, int]] = {}
_SHARED_SESSION_REGISTRIES_LOCK = Lock()


def build_core_config(settings: UltimateAdapterSettings) -> UltimateCoreConfig:
    overrides: dict[str, object] = {"device": settings.ultimate_device}
    if settings.embedding_storage_dir:
        overrides["embedding_storage_dir"] = settings.embedding_storage_dir
    if settings.det_model_path:
        overrides["det_model"] = settings.det_model_path
    if settings.reid_model_path:
        overrides["reid_model"] = settings.reid_model_path
    return UltimateCoreConfig(overrides=overrides)


def _session_storage_dir(base_dir: str | None, session_id: str) -> str | None:
    if not base_dir:
        return None
    sanitized_session_id = re.sub(r"[^A-Za-z0-9]+", "-", str(session_id)).strip("-") or "session"
    return str(Path(base_dir) / f"session-{sanitized_session_id}")


def _build_session_core_config(settings: UltimateAdapterSettings, session_id: str) -> UltimateCoreConfig:
    overrides: dict[str, object] = {"device": settings.ultimate_device}
    session_storage_dir = _session_storage_dir(settings.embedding_storage_dir, session_id)
    if session_storage_dir:
        overrides["embedding_storage_dir"] = session_storage_dir
    elif settings.embedding_storage_dir:
        overrides["embedding_storage_dir"] = settings.embedding_storage_dir
    if settings.det_model_path:
        overrides["det_model"] = settings.det_model_path
    if settings.reid_model_path:
        overrides["reid_model"] = settings.reid_model_path
    return UltimateCoreConfig(overrides=overrides)


def _build_shared_runtime_resources(settings: UltimateAdapterSettings) -> tuple[object, object]:
    cfg = build_core_config(settings).to_dict()
    device = resolve_device(str(cfg["device"]))
    cache_key = (
        str(cfg["det_model"]),
        str(cfg["reid_model"]),
        device,
        bool(cfg["fp16"]),
    )
    with _SHARED_RUNTIME_RESOURCES_LOCK:
        cached = _SHARED_RUNTIME_RESOURCES.get(cache_key)
        if cached is not None:
            return cached
        detector = load_detector_model(str(cfg["det_model"]), device)
        base_extractor = RobustFeatureExtractor(str(cfg["reid_model"]), device, bool(cfg["fp16"]), cfg)
        feature_extractor = MultiScalePyramidExtractor(base_extractor, cfg)
        _SHARED_RUNTIME_RESOURCES[cache_key] = (detector, feature_extractor)
        return detector, feature_extractor


def _acquire_shared_session_registry(session_id: str, settings: UltimateAdapterSettings) -> GlobalIdentityRegistry:
    with _SHARED_SESSION_REGISTRIES_LOCK:
        cached = _SHARED_SESSION_REGISTRIES.get(session_id)
        if cached is not None:
            registry, refcount = cached
            _SHARED_SESSION_REGISTRIES[session_id] = (registry, refcount + 1)
            return registry

        registry = GlobalIdentityRegistry(_build_session_core_config(settings, session_id).to_dict())
        _SHARED_SESSION_REGISTRIES[session_id] = (registry, 1)
        return registry


def _release_shared_session_registry(session_id: str, registry: GlobalIdentityRegistry) -> None:
    with _SHARED_SESSION_REGISTRIES_LOCK:
        cached = _SHARED_SESSION_REGISTRIES.get(session_id)
        if cached is None:
            return
        cached_registry, refcount = cached
        if cached_registry is not registry:
            return
        if refcount > 1:
            _SHARED_SESSION_REGISTRIES[session_id] = (registry, refcount - 1)
            return
        _SHARED_SESSION_REGISTRIES.pop(session_id, None)
    registry.close()


def _is_retryable_frame_error(exc: Exception) -> bool:
    if isinstance(exc, DBAPIError):
        original = getattr(exc, "orig", None)
        if original is not None and "deadlock detected" in str(original).lower():
            return True
    return "deadlock detected" in str(exc).lower()


class UltimateCameraWorker:
    def __init__(
        self,
        session_id: str,
        camera_id: str,
        *,
        rtsp_url: str,
        redis_client,
        settings: UltimateAdapterSettings,
    ) -> None:
        self.session_id = session_id
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.redis_client = redis_client
        self.settings = settings
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_frame_at: datetime | None = None
        self.last_error: str | None = None
        self.frames_processed = 0
        self._shared_registry = _acquire_shared_session_registry(session_id, settings)
        detector, feature_extractor = _build_shared_runtime_resources(settings)
        self._runtime = UltimateSessionRuntime(
            facade=UltimateAdapterCoreFacade(
                session_id=session_id,
                camera_id=camera_id,
                topology={"num_cameras": 1},
                config=_build_session_core_config(settings, session_id),
                detector=detector,
                feature_extractor=feature_extractor,
                registry=self._shared_registry,
            ),
            output_adapter=UltimateOutputAdapter(
                redis_client=redis_client,
                snapshot_interval_frames=settings.snapshot_interval_frames,
            ),
        )

    @property
    def worker_key(self) -> tuple[str, str]:
        return (self.session_id, self.camera_id)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name=f"ultimate-camera-{self.camera_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _open_capture(self):
        import cv2

        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay",
        )

        capture = await asyncio.to_thread(cv2.VideoCapture, self.rtsp_url, cv2.CAP_FFMPEG)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            await asyncio.to_thread(capture.set, cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            await asyncio.to_thread(capture.set, cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5_000)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            await asyncio.to_thread(capture.set, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5_000)
        if not await asyncio.to_thread(capture.isOpened):
            await asyncio.to_thread(capture.release)
            return None
        return capture

    async def _release_capture(self, capture) -> None:
        await asyncio.to_thread(capture.release)

    async def run(self) -> None:
        capture = None
        last_processed = 0.0
        frame_interval = 1.0 / max(1, self.settings.runtime_fps)

        try:
            while not self._stop.is_set():
                if capture is None:
                    capture = await self._open_capture()
                    if capture is None:
                        logger.warning(
                            "ultimate-adapter failed to open RTSP stream session=%s camera=%s url=%s",
                            self.session_id,
                            self.camera_id,
                            self.rtsp_url,
                        )
                        await asyncio.sleep(self.settings.runtime_reconnect_delay_seconds)
                        continue

                now = time.monotonic()
                if last_processed and now - last_processed < frame_interval:
                    grabbed = await asyncio.to_thread(capture.grab)
                    if not grabbed:
                        logger.warning(
                            "ultimate-adapter frame grab failed session=%s camera=%s; reconnecting",
                            self.session_id,
                            self.camera_id,
                        )
                        await self._release_capture(capture)
                        capture = None
                        await asyncio.sleep(self.settings.runtime_reconnect_delay_seconds)
                    continue

                ok, frame = await asyncio.to_thread(capture.read)
                if not ok or frame is None:
                    logger.warning(
                        "ultimate-adapter frame read failed session=%s camera=%s; reconnecting",
                        self.session_id,
                        self.camera_id,
                    )
                    await self._release_capture(capture)
                    capture = None
                    await asyncio.sleep(self.settings.runtime_reconnect_delay_seconds)
                    continue

                observed_at = datetime.now(timezone.utc)
                async with async_session() as db:
                    processed = False
                    for attempt in range(1, 4):
                        try:
                            await self._runtime.process_frame(db, frame=frame, observed_at=observed_at)
                            await db.commit()
                            self.last_frame_at = observed_at
                            self.frames_processed += 1
                            self.last_error = None
                            processed = True
                            break
                        except Exception as exc:
                            await db.rollback()
                            if _is_retryable_frame_error(exc) and attempt < 3:
                                self.last_error = "frame_processing_retry"
                                logger.warning(
                                    "ultimate-adapter retrying frame after database deadlock session=%s camera=%s attempt=%s",
                                    self.session_id,
                                    self.camera_id,
                                    attempt,
                                )
                                await asyncio.sleep(0.05 * attempt)
                                continue
                            self.last_error = "frame_processing_failed"
                            logger.exception(
                                "ultimate-adapter failed processing frame session=%s camera=%s",
                                self.session_id,
                                self.camera_id,
                            )
                            break
                    if not processed and self.last_error == "frame_processing_retry":
                        self.last_error = "frame_processing_failed"
                last_processed = time.monotonic()
        finally:
            if capture is not None:
                await self._release_capture(capture)
            async with async_session() as db:
                try:
                    await self._runtime.cleanup(db, observed_at=datetime.now(timezone.utc))
                    await db.commit()
                except Exception:
                    self.last_error = "cleanup_failed"
                    logger.exception(
                        "ultimate-adapter cleanup failed session=%s camera=%s",
                        self.session_id,
                        self.camera_id,
                    )
                    await db.rollback()
            _release_shared_session_registry(self.session_id, self._shared_registry)

    def status(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "rtsp_url": self.rtsp_url,
            "frames_processed": self.frames_processed,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "last_error": self.last_error,
            "running": self._task is not None and not self._task.done(),
        }


async def _publish_runtime_status(redis_client, settings: UltimateAdapterSettings, *, manager, context) -> None:
    workers = [worker.status() for worker in manager.workers.values()]
    payload = {
        "status": "ok",
        "selector": context.selector if context is not None else "standard",
        "active_session_id": context.session_id if context is not None else None,
        "active_camera_count": len(context.contracts) if context is not None else 0,
        "worker_count": len(workers),
        "workers": workers,
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    if any(worker.get("last_error") for worker in workers):
        payload["status"] = "degraded"
    await redis_client.set(settings.runtime_status_key, json.dumps(payload))


async def run_runtime_supervisor() -> None:
    settings = load_settings()
    redis_client = await get_redis()

    def worker_factory(*, session_id: str, camera_id: str, rtsp_url: str) -> UltimateCameraWorker:
        return UltimateCameraWorker(
            session_id,
            camera_id,
            rtsp_url=rtsp_url,
            redis_client=redis_client,
            settings=settings,
        )

    manager = UltimateRuntimeManager(worker_factory=worker_factory)
    try:
        while True:
            context = await read_active_runtime_context(redis_client)
            if context is None:
                await manager.stop_all()
            else:
                await manager.apply_context(
                    session_id=context.session_id,
                    contracts=context.contracts,
                )
            await _publish_runtime_status(
                redis_client,
                settings,
                manager=manager,
                context=context,
            )
            await asyncio.sleep(settings.runtime_poll_interval_seconds)
    finally:
        await manager.stop_all()
        await _publish_runtime_status(
            redis_client,
            settings,
            manager=manager,
            context=None,
        )
