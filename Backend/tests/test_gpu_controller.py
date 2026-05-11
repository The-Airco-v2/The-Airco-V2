"""Tests for the GPU boot controller.

The controller manages the RunPod pod that hosts the GPU pipeline. It
needs to: resume the pod on session_start, no-op when already running,
and stop the pod after a configurable idle window. All network calls
are mocked here — these tests verify the state-machine logic only.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from airco.config import settings as airco_settings
from api import gpu_controller as gpu_controller_module
from api.gpu_controller import GpuController
from api.runpod_client import PodInfo, PodState, RunPodError


@pytest.fixture
def gpu_settings(monkeypatch):
    """Configure GPU control as enabled for these tests."""
    monkeypatch.setattr(airco_settings, "runpod_api_key", "test-key")
    monkeypatch.setattr(airco_settings, "runpod_pod_id", "pod-abc")
    monkeypatch.setattr(airco_settings, "gpu_boot_timeout_seconds", 5)
    monkeypatch.setattr(airco_settings, "gpu_idle_timeout_seconds", 60)
    monkeypatch.setattr(airco_settings, "gpu_health_target", "")
    yield


@pytest.fixture
def fake_redis():
    store: dict[str, str] = {}

    async def get(key: str):
        return store.get(key)

    async def set_(key: str, value, **kwargs):
        if "nx" in kwargs and kwargs["nx"] and key in store:
            return None
        store[key] = str(value)
        return True

    async def delete(key: str):
        store.pop(key, None)
        return 1

    redis = SimpleNamespace(get=get, set=set_, delete=delete, store=store)
    return redis


@pytest.fixture
def patch_redis(monkeypatch, fake_redis):
    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(gpu_controller_module, "get_redis", fake_get_redis)
    return fake_redis


@pytest.fixture
def patch_runpod(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        gpu_controller_module, "RunPodClient", lambda *args, **kwargs: client
    )
    return client


@pytest.fixture
def patch_async_session(monkeypatch):
    """Provide a fake async_session context manager so the controller's
    db queries can be intercepted with a configurable scalar result."""
    state = {"count": 0}

    class FakeResult:
        def scalar_one(self):
            return state["count"]

    fake_db = SimpleNamespace(execute=AsyncMock(return_value=FakeResult()))

    @asynccontextmanager
    async def fake_async_session():
        yield fake_db

    monkeypatch.setattr(gpu_controller_module, "async_session", fake_async_session)
    return state


def _pod(state: PodState) -> PodInfo:
    return PodInfo(pod_id="pod-abc", state=state, gpu_count=1, public_ip=None, raw={})


@pytest.mark.asyncio
async def test_disabled_controller_is_noop(monkeypatch):
    monkeypatch.setattr(airco_settings, "runpod_api_key", "")
    monkeypatch.setattr(airco_settings, "runpod_pod_id", "")
    c = GpuController()
    assert c.enabled is False
    await c.ensure_running()  # must not raise
    assert await c.release_if_idle() is False


@pytest.mark.asyncio
async def test_ensure_running_skips_resume_when_already_running(
    gpu_settings, patch_redis, patch_runpod
):
    patch_runpod.get_pod.return_value = _pod(PodState.RUNNING)

    c = GpuController()
    await c.ensure_running()

    patch_runpod.resume_pod.assert_not_called()
    # last-seen-running timestamp should be set so idle check has a baseline.
    assert patch_redis.store.get("airco:gpu:last_seen_running") is not None


@pytest.mark.asyncio
async def test_ensure_running_resumes_stopped_pod(
    gpu_settings, patch_redis, patch_runpod
):
    # First call: STOPPED. Second call: RUNNING (poll after resume).
    patch_runpod.get_pod.side_effect = [
        _pod(PodState.STOPPED),
        _pod(PodState.RUNNING),
    ]

    c = GpuController()
    await c.ensure_running()

    patch_runpod.resume_pod.assert_awaited_once_with("pod-abc")
    # Lock must have been released after the resume.
    assert "airco:gpu:lock" not in patch_redis.store


@pytest.mark.asyncio
async def test_ensure_running_rejects_terminated_pod(
    gpu_settings, patch_redis, patch_runpod
):
    patch_runpod.get_pod.return_value = _pod(PodState.TERMINATED)

    c = GpuController()
    with pytest.raises(RunPodError):
        await c.ensure_running()
    patch_runpod.resume_pod.assert_not_called()


@pytest.mark.asyncio
async def test_release_if_idle_keeps_pod_when_sessions_active(
    gpu_settings, patch_redis, patch_runpod, patch_async_session
):
    patch_async_session["count"] = 2  # running sessions exist

    c = GpuController()
    stopped = await c.release_if_idle()
    assert stopped is False
    patch_runpod.stop_pod.assert_not_called()


@pytest.mark.asyncio
async def test_release_if_idle_waits_for_timeout(
    gpu_settings, patch_redis, patch_runpod, patch_async_session
):
    # No active sessions, last seen recently.
    patch_async_session["count"] = 0
    patch_redis.store["airco:gpu:last_seen_running"] = str(time.time())

    c = GpuController()
    stopped = await c.release_if_idle()
    assert stopped is False
    patch_runpod.stop_pod.assert_not_called()


@pytest.mark.asyncio
async def test_release_if_idle_stops_pod_after_timeout(
    monkeypatch, gpu_settings, patch_redis, patch_runpod, patch_async_session
):
    monkeypatch.setattr(airco_settings, "gpu_idle_timeout_seconds", 1)
    patch_async_session["count"] = 0
    patch_redis.store["airco:gpu:last_seen_running"] = str(time.time() - 60)
    patch_runpod.get_pod.return_value = _pod(PodState.RUNNING)

    c = GpuController()
    stopped = await c.release_if_idle()
    assert stopped is True
    patch_runpod.stop_pod.assert_awaited_once_with("pod-abc")


@pytest.mark.asyncio
async def test_release_if_idle_seeds_timestamp_on_first_call(
    gpu_settings, patch_redis, patch_runpod, patch_async_session
):
    # No prior timestamp, no active sessions. First call records the
    # moment so a subsequent call after the timeout can stop the pod.
    patch_async_session["count"] = 0
    assert "airco:gpu:last_seen_running" not in patch_redis.store

    c = GpuController()
    stopped = await c.release_if_idle()
    assert stopped is False
    assert "airco:gpu:last_seen_running" in patch_redis.store
    patch_runpod.stop_pod.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_ensure_running_in_one_process_is_idempotent(
    gpu_settings, patch_redis, patch_runpod
):
    # Two concurrent callers must not double-trigger a resume.
    states = [_pod(PodState.STOPPED), _pod(PodState.RUNNING), _pod(PodState.RUNNING)]

    def next_pod(*_a, **_kw):
        return states.pop(0) if states else _pod(PodState.RUNNING)

    patch_runpod.get_pod.side_effect = lambda *a, **kw: next_pod()

    c = GpuController()
    await asyncio.gather(c.ensure_running(), c.ensure_running())

    # resume_pod should be called at most once (serialized by the
    # internal asyncio Lock).
    assert patch_runpod.resume_pod.await_count <= 1
