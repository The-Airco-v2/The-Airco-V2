"""Push active session camera frames into Savant over ZeroMQ."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
from dataclasses import dataclass

import cv2
import redis.asyncio as redis
from savant_rs.py.client.image_source.image_source import JpegSource
from savant_rs.py.client.runner.source import SourceRunner
from savant_feeder.runtime import parse_active_camera_ids, stream_rtsp_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("savant-feeder")

ACTIVE_SESSION_KEY = "airco:local:active_session_id"
ACTIVE_CAMERA_IDS_KEY = "airco:local:active_camera_ids"
@dataclass(frozen=True)
class ActiveCameraContext:
    session_id: str
    camera_ids: tuple[str, ...]


class CameraFeedWorker:
    def __init__(
        self,
        session_id: str,
        camera_id: str,
        *,
        base_rtsp_url: str,
        fps: int,
        jpeg_quality: int,
        reconnect_delay: float,
        runner: SourceRunner,
        runner_lock: threading.Lock,
    ) -> None:
        self.session_id = session_id
        self.camera_id = camera_id
        self.base_rtsp_url = base_rtsp_url
        self.fps = max(1, fps)
        self.jpeg_quality = max(30, min(95, jpeg_quality))
        self.reconnect_delay = max(0.2, reconnect_delay)
        self.runner = runner
        self.runner_lock = runner_lock
        self._stop = threading.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def worker_key(self) -> tuple[str, str]:
        return (self.session_id, self.camera_id)

    @property
    def rtsp_url(self) -> str:
        return stream_rtsp_url(self.base_rtsp_url, self.session_id, self.camera_id)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name=f"feed-{self.camera_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def run(self) -> None:
        await asyncio.to_thread(self._run_blocking)

    def _run_blocking(self) -> None:
        logger.info(
            "starting camera feeder session=%s camera=%s url=%s",
            self.session_id,
            self.camera_id,
            self.rtsp_url,
        )
        pts = 0
        duration = int(1_000_000_000 / self.fps)
        frame_interval = 1.0 / self.fps
        capture = None
        last_sent_at = 0.0

        try:
            while not self._stop.is_set():
                if capture is None or not capture.isOpened():
                    if capture is not None:
                        capture.release()
                    capture = cv2.VideoCapture(self.rtsp_url)
                    if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if not capture.isOpened():
                        logger.warning("failed to open RTSP stream for camera=%s", self.camera_id)
                        time.sleep(self.reconnect_delay)
                        continue

                ok, frame = capture.read()
                if not ok or frame is None:
                    logger.warning("camera=%s frame read failed; reconnecting", self.camera_id)
                    capture.release()
                    capture = None
                    time.sleep(self.reconnect_delay)
                    continue

                now = time.monotonic()
                if last_sent_at and now - last_sent_at < frame_interval:
                    continue

                encoded, jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
                )
                if not encoded:
                    logger.warning("jpeg encode failed for camera=%s", self.camera_id)
                    continue

                source = JpegSource(
                    source_id=self.camera_id,
                    file=io.BytesIO(jpeg.tobytes()),
                    pts=pts,
                    framerate=(self.fps, 1),
                )
                with self.runner_lock:
                    self.runner.send(source, send_eos=False)
                pts += duration
                last_sent_at = now
        except Exception:
            logger.exception("camera feeder crashed for camera=%s", self.camera_id)
        finally:
            if capture is not None:
                capture.release()
            try:
                with self.runner_lock:
                    self.runner.send_eos(self.camera_id)
            except Exception:
                logger.exception("failed to send eos for camera=%s", self.camera_id)
            logger.info("stopped camera feeder session=%s camera=%s", self.session_id, self.camera_id)


async def _read_active_context(redis_client: redis.Redis) -> ActiveCameraContext | None:
    session_id, raw_camera_ids = await redis_client.mget(ACTIVE_SESSION_KEY, ACTIVE_CAMERA_IDS_KEY)
    if not session_id:
        return None
    camera_ids = parse_active_camera_ids(raw_camera_ids)
    if not camera_ids:
        return None
    return ActiveCameraContext(session_id=str(session_id), camera_ids=tuple(camera_ids))


async def _stop_workers(workers: dict[tuple[str, str], CameraFeedWorker], stale_keys: set[tuple[str, str]]) -> None:
    for key in stale_keys:
        worker = workers.pop(key, None)
        if worker is not None:
            await worker.stop()


async def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    base_rtsp_url = os.getenv("GO2RTC_RTSP_BASE_URL", "rtsp://host.docker.internal:8556")
    zmq_endpoint = os.getenv("ZMQ_SRC_ENDPOINT", "dealer+connect:tcp://savant-pipeline:5000")
    poll_interval = float(os.getenv("FEEDER_POLL_INTERVAL_SECONDS", "1.0"))
    fps = int(os.getenv("FEEDER_FPS", "8"))
    jpeg_quality = int(os.getenv("FEEDER_JPEG_QUALITY", "85"))
    reconnect_delay = float(os.getenv("FEEDER_RECONNECT_DELAY_SECONDS", "1.0"))

    redis_client = redis.from_url(redis_url, decode_responses=True)
    runner = SourceRunner(
        socket=zmq_endpoint,
        log_provider=None,
        retries=3,
        module_health_check_url=None,
        module_health_check_timeout=5.0,
        module_health_check_interval=0.5,
        telemetry_enabled=False,
    )
    runner_lock = threading.Lock()
    workers: dict[tuple[str, str], CameraFeedWorker] = {}

    try:
        while True:
            context = await _read_active_context(redis_client)
            desired_keys = (
                {(context.session_id, camera_id) for camera_id in context.camera_ids}
                if context
                else set()
            )

            stale_keys = set(workers) - desired_keys
            if stale_keys:
                await _stop_workers(workers, stale_keys)

            if context:
                for camera_id in context.camera_ids:
                    key = (context.session_id, camera_id)
                    if key in workers:
                        continue
                    worker = CameraFeedWorker(
                        context.session_id,
                        camera_id,
                        base_rtsp_url=base_rtsp_url,
                        fps=fps,
                        jpeg_quality=jpeg_quality,
                        reconnect_delay=reconnect_delay,
                        runner=runner,
                        runner_lock=runner_lock,
                    )
                    workers[key] = worker
                    worker.start()

            await asyncio.sleep(poll_interval)
    finally:
        await _stop_workers(workers, set(workers))
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
