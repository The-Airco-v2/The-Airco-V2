"""GPU pod lifecycle orchestrator.

Wraps RunPodClient with the policy the API needs:
- ensure_running: idempotent boot. Resumes a stopped pod and waits
  for the GPU stack to report healthy before returning.
- release_if_idle: stops the pod if no sessions are running and the
  last running session ended at least GPU_IDLE_TIMEOUT_SECONDS ago.
- start_idle_loop: long-running task installed as a startup hook in
  api/main.py that runs release_if_idle() on a fixed interval.

Concurrency:
  All state transitions are guarded by a Redis NX lock so multiple
  API replicas (or concurrent start_session calls in one replica)
  cooperate. Within a single replica we additionally use an asyncio
  Lock to short-circuit common cases without round-tripping Redis.

GPU-disabled mode:
  If RUNPOD_API_KEY is empty the controller becomes a no-op. This
  lets the API run unchanged in dev, where the GPU stack is brought
  up via docker compose on the same host.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.config import settings
from airco.db import async_session
from airco.models import Session as SessionModel
from airco.redis_streams import get_redis

from api.runpod_client import PodState, RunPodClient, RunPodError

logger = logging.getLogger(__name__)


GPU_LOCK_KEY = "airco:gpu:lock"
GPU_LAST_SEEN_RUNNING_KEY = "airco:gpu:last_seen_running"


class GpuController:
    """Singleton-style controller. Held on app state."""

    def __init__(self) -> None:
        self._local_lock = asyncio.Lock()
        self._client: RunPodClient | None = None
        self._idle_task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(settings.runpod_api_key and settings.runpod_pod_id)

    def _get_client(self) -> RunPodClient:
        if self._client is None:
            self._client = RunPodClient(
                api_key=settings.runpod_api_key,
                api_url=settings.runpod_api_url,
            )
        return self._client

    async def ensure_running(self) -> None:
        """Block until the GPU pod is RUNNING and the health target is reachable.

        If GPU control is disabled (no RunPod credentials configured),
        this is a no-op — we assume the GPU stack is already running.
        """
        if not self.enabled:
            logger.debug("gpu controller disabled; ensure_running is a no-op")
            return

        async with self._local_lock:
            client = self._get_client()
            pod = await client.get_pod(settings.runpod_pod_id)
            if pod.state == PodState.RUNNING:
                logger.debug("gpu pod %s already RUNNING", settings.runpod_pod_id)
                await self._record_seen_running()
                if settings.gpu_health_target:
                    await self._wait_for_health(settings.gpu_health_target)
                return
            if pod.state == PodState.TERMINATED:
                raise RunPodError(
                    f"GPU pod {settings.runpod_pod_id} is in non-resumable state {pod.state.value}"
                )

            # Acquire cross-replica Redis lock before mutating state.
            redis = await get_redis()
            lock_acquired = await redis.set(GPU_LOCK_KEY, "1", nx=True, ex=300)
            try:
                if not lock_acquired:
                    # Another process is resuming. Just wait for health.
                    logger.info("another worker is resuming gpu pod; waiting for health")
                else:
                    logger.info("resuming gpu pod %s", settings.runpod_pod_id)
                    await client.resume_pod(settings.runpod_pod_id)
                await self._wait_for_state(client, PodState.RUNNING)
                if settings.gpu_health_target:
                    await self._wait_for_health(settings.gpu_health_target)
                await self._record_seen_running()
            finally:
                if lock_acquired:
                    await redis.delete(GPU_LOCK_KEY)

    async def stop(self) -> None:
        if not self.enabled:
            return
        async with self._local_lock:
            client = self._get_client()
            pod = await client.get_pod(settings.runpod_pod_id)
            if pod.state != PodState.RUNNING:
                logger.debug("gpu pod already %s, no stop needed", pod.state.value)
                return
            logger.info("stopping gpu pod %s", settings.runpod_pod_id)
            await client.stop_pod(settings.runpod_pod_id)

    async def release_if_idle(self) -> bool:
        """Stop the pod if there are no running sessions and the
        idle timeout has elapsed since the last one ended.

        Returns True if the pod was stopped this call.
        """
        if not self.enabled:
            return False
        try:
            async with async_session() as db:
                if await self._has_active_sessions(db):
                    await self._record_seen_running()
                    return False
                last_seen = await self._last_seen_running_at()
                now = time.time()
                if last_seen is None:
                    # First idle check, nothing to stop against. Record
                    # the moment so we'll release on the next interval
                    # if still idle.
                    await self._record_seen_running()
                    return False
                if now - last_seen < settings.gpu_idle_timeout_seconds:
                    return False
            await self.stop()
            return True
        except Exception:
            logger.exception("release_if_idle failed")
            return False

    def start_idle_loop(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            return
        if not self.enabled:
            logger.info("gpu controller disabled; idle loop not started")
            return
        self._idle_task = asyncio.create_task(self._idle_loop(), name="gpu-idle-loop")

    async def shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except (asyncio.CancelledError, Exception):
                pass
            self._idle_task = None

    async def _idle_loop(self) -> None:
        interval = max(15, settings.gpu_idle_check_interval_seconds)
        logger.info("gpu idle loop running every %ss", interval)
        while True:
            try:
                await asyncio.sleep(interval)
                await self.release_if_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("gpu idle loop iteration failed; continuing")

    async def _wait_for_state(
        self,
        client: RunPodClient,
        target: PodState,
        poll_interval: float = 2.0,
    ) -> None:
        deadline = time.time() + settings.gpu_boot_timeout_seconds
        while time.time() < deadline:
            pod = await client.get_pod(settings.runpod_pod_id)
            if pod.state == target:
                return
            await asyncio.sleep(poll_interval)
        raise RunPodError(
            f"GPU pod did not reach {target.value} within {settings.gpu_boot_timeout_seconds}s"
        )

    async def _wait_for_health(
        self,
        url: str,
        poll_interval: float = 2.0,
        success_status: int = 200,
    ) -> None:
        deadline = time.time() + settings.gpu_boot_timeout_seconds
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == success_status:
                        logger.info("gpu health target %s is ready", url)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(poll_interval)
        raise RunPodError(
            f"GPU health target {url} did not return {success_status} within "
            f"{settings.gpu_boot_timeout_seconds}s"
        )

    async def _has_active_sessions(self, db: AsyncSession) -> bool:
        result = await db.execute(
            select(func.count(SessionModel.id)).where(
                SessionModel.status.in_(("running", "starting"))
            )
        )
        return (result.scalar_one() or 0) > 0

    async def _record_seen_running(self) -> None:
        redis = await get_redis()
        await redis.set(GPU_LAST_SEEN_RUNNING_KEY, str(time.time()))

    async def _last_seen_running_at(self) -> float | None:
        redis = await get_redis()
        raw = await redis.get(GPU_LAST_SEEN_RUNNING_KEY)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None


_controller: GpuController | None = None


def get_gpu_controller() -> GpuController:
    global _controller
    if _controller is None:
        _controller = GpuController()
    return _controller


# Convenience for tests / callers that want to replace the singleton.
def set_gpu_controller(controller: GpuController) -> None:
    global _controller
    _controller = controller


GpuEnsureRunningFn = Callable[[], Awaitable[None]]
