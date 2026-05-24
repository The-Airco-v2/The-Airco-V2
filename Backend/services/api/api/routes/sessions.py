"""Session CRUD: create, start, stop, pause, resume, list, detail."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Literal, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airco import redis_streams  # late lookup keeps test patches simple
from airco.db import async_session, get_session
from airco.redis_streams import get_redis
from airco.models import Session, SessionCamera, Camera
from api.auth import AuthState, require_authenticated, require_admin
from api.gpu_controller import get_gpu_controller
from api.runpod_client import RunPodError, PodState
from airco.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

REID_PROFILE_STANDARD = "standard"
REID_PROFILE_ULTIMATE = "ultimate"
LEGACY_REID_PROFILE_ULTIMATE = "ultimate_reid"
ReIdProfile = Literal["standard", "ultimate"]


def _normalize_reid_profile(value: object) -> str:
    if not isinstance(value, str):
        return REID_PROFILE_STANDARD
    normalized = value.strip().lower()
    if normalized == LEGACY_REID_PROFILE_ULTIMATE:
        return REID_PROFILE_ULTIMATE
    if normalized == REID_PROFILE_ULTIMATE:
        return REID_PROFILE_ULTIMATE
    return REID_PROFILE_STANDARD


class SessionCreate(BaseModel):
    name: str
    camera_ids: list[uuid.UUID] = []


class SessionResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    camera_ids: list[uuid.UUID]
    camera_count: int
    started_at: datetime | None
    stopped_at: datetime | None
    created_at: datetime
    reid_profile: ReIdProfile = REID_PROFILE_STANDARD


class UltimateRuntimeWorkerStatus(BaseModel):
    session_id: str
    camera_id: str
    rtsp_url: str
    frames_processed: int
    last_frame_at: str | None = None
    last_error: str | None = None
    running: bool = False


class UltimateRuntimeStatusResponse(BaseModel):
    status: str
    selector: str
    active_session_id: str | None = None
    active_camera_count: int = 0
    worker_count: int = 0
    last_heartbeat_at: str | None = None
    workers: list[UltimateRuntimeWorkerStatus] = []


class SessionStartRequest(BaseModel):
    reid_profile: ReIdProfile = REID_PROFILE_STANDARD

    @field_validator("reid_profile", mode="before")
    @classmethod
    def normalize_reid_profile(cls, value: str) -> str:
        if isinstance(value, str):
            return _normalize_reid_profile(value)
        return value


async def _session_camera_ids(db: AsyncSession, session_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(SessionCamera).where(SessionCamera.session_id == session_id)
    )
    return [session_camera.camera_id for session_camera in result.scalars().all()]


async def _session_response(db: AsyncSession, session: Session) -> SessionResponse:
    camera_ids = await _session_camera_ids(db, session.id)
    config = dict(session.config or {})
    return SessionResponse(
        id=session.id,
        name=session.name,
        status=session.status if session.status != "created" else "stopped",
        camera_ids=camera_ids,
        camera_count=len(camera_ids),
        started_at=session.started_at,
        stopped_at=session.stopped_at,
        created_at=session.created_at,
        reid_profile=_normalize_reid_profile(config.get("reid_profile", REID_PROFILE_STANDARD)),
    )


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    session = Session(
        tenant_id=auth.tenant_id,
        name=body.name,
    )
    db.add(session)
    await db.flush()

    for cam_id in body.camera_ids:
        db.add(SessionCamera(session_id=session.id, camera_id=cam_id))

    await db.commit()
    await db.refresh(session)
    return await _session_response(db, session)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Session)
        .where(Session.tenant_id == auth.tenant_id)
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    return [await _session_response(db, s) for s in sessions]


@router.get("/runtime/ultimate-status", response_model=UltimateRuntimeStatusResponse)
async def get_ultimate_runtime_status(
    auth: AuthState = Depends(require_authenticated),
):
    _ = auth
    redis = await get_redis()
    raw_payload = await redis.get("airco:ultimate-adapter:runtime_status")
    if not raw_payload:
        return UltimateRuntimeStatusResponse(
            status="unknown",
            selector=REID_PROFILE_STANDARD,
        )
    if isinstance(raw_payload, str):
        payload = json.loads(raw_payload)
    else:
        payload = raw_payload
    return UltimateRuntimeStatusResponse.model_validate(payload)


class GpuStatusResponse(BaseModel):
    type: str  # "runpod" or "local"
    is_enabled: bool
    status: str  # "ON" or "OFF"
    gpu_name: str | None = None
    gpu_count: int | None = None
    gpu_id: str | None = None
    memory: str | None = None
    configuration: dict[str, str] | None = None


def get_local_gpu_info_fallback() -> dict[str, Any]:
    if not shutil.which("nvidia-smi"):
        return {
            "type": "local",
            "is_enabled": False,
            "status": "OFF",
            "gpu_name": "No local GPU detected",
            "gpu_count": 0,
            "gpu_id": None,
            "memory": None,
            "configuration": {}
        }
    
    try:
        cmd = ["nvidia-smi", "--query-gpu=index,name,memory.total,uuid", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
        if not lines:
            return {
                "type": "local",
                "is_enabled": False,
                "status": "OFF",
                "gpu_name": "No local GPU detected",
                "gpu_count": 0,
                "gpu_id": None,
                "memory": None,
                "configuration": {}
            }
        
        parts = [p.strip() for p in lines[0].split(",")]
        gpu_index = parts[0] if len(parts) > 0 else "0"
        gpu_name = parts[1] if len(parts) > 1 else "Local NVIDIA GPU"
        memory_total = parts[2] if len(parts) > 2 else None
        gpu_uuid = parts[3] if len(parts) > 3 else f"local-gpu-{gpu_index}"
        
        memory_str = f"{memory_total} MiB VRAM" if memory_total else None
        
        return {
            "type": "local",
            "is_enabled": True,
            "status": "ON",
            "gpu_name": gpu_name,
            "gpu_count": len(lines),
            "gpu_id": gpu_uuid,
            "memory": memory_str,
            "configuration": {
                "GPU UUID": gpu_uuid,
                "VRAM": f"{memory_total} MiB" if memory_total else "Unknown",
                "GPU Index": gpu_index,
                "Total GPUs": str(len(lines))
            }
        }
    except Exception as e:
        logger.warning("Failed to query nvidia-smi: %s", e)
        return {
            "type": "local",
            "is_enabled": False,
            "status": "OFF",
            "gpu_name": "No local GPU detected",
            "gpu_count": 0,
            "gpu_id": None,
            "memory": None,
            "configuration": {"error": str(e)}
        }


@router.get("/runtime/gpu-status", response_model=GpuStatusResponse)
async def get_gpu_status(
    auth: AuthState = Depends(require_authenticated),
):
    _ = auth
    controller = get_gpu_controller()
    
    if controller.enabled:
        try:
            pod = await controller.get_pod_info()
            if pod:
                raw = pod.raw
                gpu_name = raw.get("machine", {}).get("gpuDisplayName") or "Unknown GPU"
                status = "ON" if pod.state == PodState.RUNNING else "OFF"
                
                # configuration details
                config = {
                    "Pod ID": pod.pod_id,
                    "System RAM": f"{raw.get('memoryInGb', 'unknown')} GB",
                    "VCPU Count": f"{raw.get('vcpuCount', 'unknown')}",
                    "Volume Disk": f"{raw.get('volumeInGb', 'unknown')} GB",
                    "Container Disk": f"{raw.get('containerDiskInGb', 'unknown')} GB",
                    "Image": raw.get("imageName") or "unknown"
                }
                
                memory_str = f"{raw.get('memoryInGb')} GB System RAM" if raw.get('memoryInGb') else None
                
                return GpuStatusResponse(
                    type="runpod",
                    is_enabled=True,
                    status=status,
                    gpu_name=gpu_name,
                    gpu_count=pod.gpu_count,
                    gpu_id=pod.pod_id,
                    memory=memory_str,
                    configuration=config
                )
        except Exception as exc:
            logger.exception("Failed to query RunPod GPU status")
            return GpuStatusResponse(
                type="runpod",
                is_enabled=True,
                status="OFF",
                gpu_name="RunPod GPU (Query Failed)",
                gpu_count=None,
                gpu_id=settings.runpod_pod_id,
                memory=None,
                configuration={"error": str(exc)}
            )
            
    # Local GPU fallback
    local_name = os.environ.get("LOCAL_GPU_NAME")
    local_mem = os.environ.get("LOCAL_GPU_MEM")
    local_uuid = os.environ.get("LOCAL_GPU_UUID")
    
    if local_name:
        memory_str = f"{local_mem} MiB VRAM" if local_mem else None
        return GpuStatusResponse(
            type="local",
            is_enabled=True,
            status="ON",
            gpu_name=local_name,
            gpu_count=1,
            gpu_id=local_uuid or "local-gpu-0",
            memory=memory_str,
            configuration={
                "GPU UUID": local_uuid or "N/A",
                "VRAM": f"{local_mem} MiB" if local_mem else "Unknown",
                "GPU Index": "0"
            }
        )
        
    local_info = get_local_gpu_info_fallback()
    return GpuStatusResponse(**local_info)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session_detail(
    session_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.tenant_id == auth.tenant_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    return await _session_response(db, session)


@router.post("/{session_id}/start")
async def start_session(
    session_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    body: SessionStartRequest | None = None,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Begin a session. Booting the GPU pod (when one is configured)
    can take 30-60s, so we transition the session to ``starting``
    immediately and finish the work — pod resume, health check,
    publishing ``session_start`` to ``airco:control`` — in a background
    task. The frontend polls / subscribes for status to flip to
    ``running``.

    Returns 202 Accepted on dispatch. The caller should treat a non-
    2xx response from this endpoint as a hard failure (e.g. session
    not found); GPU boot failures surface later as ``status="failed"``
    on the session record.
    """
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.tenant_id == auth.tenant_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(404, "Session not found")

    reid_profile = body.reid_profile if body is not None else REID_PROFILE_STANDARD
    cams_result = await db.execute(
        select(SessionCamera, Camera).join(Camera, SessionCamera.camera_id == Camera.id)
        .where(
            SessionCamera.session_id == session_id,
            Camera.tenant_id == auth.tenant_id,
        )
    )
    cameras_payload = [
        {"camera_id": str(sc.camera_id), "rtsp_url": cam.rtsp_url, "name": cam.name}
        for sc, cam in cams_result
    ]

    controller = get_gpu_controller()
    session.status = "starting" if controller.enabled else "running"
    session.started_at = datetime.now(timezone.utc)
    config = dict(session.config or {})
    config["reid_profile"] = reid_profile
    session.config = config
    await db.commit()

    if controller.enabled:
        background_tasks.add_task(
            _finish_session_start,
            session_id,
            reid_profile,
            cameras_payload,
        )
        return JSONResponse(
            status_code=202,
            content={"status": "starting", "reid_profile": reid_profile},
        )

    await redis_streams.publish_event("airco:control", {
        "event_type": "session_start",
        "session_id": str(session_id),
        "reid_profile": reid_profile,
        "cameras": json.dumps(cameras_payload),
    })
    return {"status": "running", "reid_profile": reid_profile}


async def _finish_session_start(
    session_id: uuid.UUID,
    reid_profile: str,
    cameras_payload: list[dict[str, str]],
) -> None:
    """Background-task continuation of start_session.

    Boots the GPU pod, waits for health, then publishes the
    ``session_start`` control event and flips the session status to
    ``running``. On any failure the session is marked ``failed`` with
    a short error message stored on ``config.start_error``.
    """
    controller = get_gpu_controller()
    try:
        await controller.ensure_running()
        await redis_streams.publish_event("airco:control", {
            "event_type": "session_start",
            "session_id": str(session_id),
            "reid_profile": reid_profile,
            "cameras": json.dumps(cameras_payload),
        })
        async with async_session() as db:
            await _mark_session_running(db, session_id)
    except (RunPodError, asyncio.CancelledError, Exception) as exc:
        logger.exception("Failed to start session %s", session_id)
        try:
            async with async_session() as db:
                await _mark_session_failed(db, session_id, str(exc))
        except Exception:
            logger.exception("Failed to record session-start failure for %s", session_id)
        if isinstance(exc, asyncio.CancelledError):
            raise


async def _mark_session_running(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(status="running")
    )
    await db.commit()


async def _mark_session_failed(
    db: AsyncSession,
    session_id: uuid.UUID,
    error_message: str,
) -> None:
    row = await db.execute(select(Session).where(Session.id == session_id))
    session = row.scalar_one_or_none()
    if session is None:
        return
    config = dict(session.config or {})
    config["start_error"] = error_message[:500]
    session.status = "failed"
    session.config = config
    session.stopped_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/{session_id}/stop")
async def stop_session(
    session_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    await db.execute(
        update(Session).where(
            Session.id == session_id,
            Session.tenant_id == auth.tenant_id,
        ).values(
            status="stopped", stopped_at=datetime.now(timezone.utc)
        )
    )
    await db.commit()
    await redis_streams.publish_event("airco:control", {
        "event_type": "session_stop",
        "session_id": str(session_id),
    })
    background_tasks.add_task(_maybe_stop_gpu_after_session)
    return {"status": "stopped"}


async def _maybe_stop_gpu_after_session() -> None:
    """Stop the GPU pod after a 60s grace period if no sessions remain active.

    The grace period lets any in-flight analytics / snapshot writes finish
    cleanly before the pod is killed. If another session starts within the
    grace window the stop is skipped.
    """
    GPU_STOP_GRACE_SECONDS = 60

    try:
        await asyncio.sleep(GPU_STOP_GRACE_SECONDS)

        # Re-check after the grace period — a new session may have started.
        async with async_session() as db:
            result = await db.execute(
                select(func.count(Session.id)).where(
                    Session.status.in_(("running", "starting"))
                )
            )
            active_count = result.scalar_one() or 0

        if active_count > 0:
            logger.info(
                "GPU stop skipped — %d session(s) became active during grace period",
                active_count,
            )
            return

        controller = get_gpu_controller()
        if controller.enabled:
            logger.info(
                "No active sessions after %ss grace — stopping GPU pod",
                GPU_STOP_GRACE_SECONDS,
            )
            await controller.stop()
    except Exception:
        logger.exception("_maybe_stop_gpu_after_session failed")




@router.post("/{session_id}/pause")
async def pause_session(
    session_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.tenant_id == auth.tenant_id)
        .values(status="paused")
    )
    await db.commit()
    await redis_streams.publish_event("airco:control", {
        "event_type": "session_pause",
        "session_id": str(session_id),
    })
    return {"status": "paused"}


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.tenant_id == auth.tenant_id)
        .values(status="running")
    )
    await db.commit()
    return {"status": "running"}
