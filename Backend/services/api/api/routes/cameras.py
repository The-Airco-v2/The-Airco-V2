"""Camera CRUD: register, list, update topology."""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import Camera, CameraPair, CameraPresenceSegment, PersonEmbedding, SessionCamera, SessionPersonTrackBinding
from api.auth import AuthState, require_authenticated, require_admin

logger = logging.getLogger(__name__)

GO2RTC_URL = os.getenv("GO2RTC_URL", "http://go2rtc:1984")

router = APIRouter()


def _stream_name(camera_name: str) -> str:
    """Derive go2rtc stream name from camera name."""
    return camera_name.lower().replace(" ", "_")


def _resolve_rtsp_url(rtsp_url: str) -> str:
    """Resolve hostname in RTSP URL to IP so go2rtc skips DNS lookup."""
    try:
        parsed = urlparse(rtsp_url)
        hostname = parsed.hostname
        if hostname and not hostname.replace(".", "").isdigit():
            # Skip resolving for Dynamic DNS hosts so go2rtc can resolve and adapt to IP changes dynamically
            if any(p in hostname.lower() for p in ("ddns", "dyndns", "no-ip", "duckdns")):
                return rtsp_url
            ip = socket.gethostbyname(hostname)
            netloc = parsed.netloc.replace(hostname, ip, 1)
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return rtsp_url


async def _go2rtc_add(name: str, rtsp_url: str) -> None:
    """Register a camera stream in go2rtc. Logs and continues on failure."""
    resolved_url = _resolve_rtsp_url(rtsp_url)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                f"{GO2RTC_URL}/api/streams",
                params={"name": name, "src": resolved_url},
            )
            if resp.status_code >= 500:
                logger.warning("[go2rtc] add stream %s failed (%d): %s", name, resp.status_code, resp.text)
            else:
                logger.info("[go2rtc] registered stream: %s", name)
    except Exception as e:
        logger.warning("[go2rtc] could not reach go2rtc to add stream %s: %s", name, e)


async def _go2rtc_remove(name: str) -> None:
    """Remove a camera stream from go2rtc. Logs and continues on failure."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.delete(
                f"{GO2RTC_URL}/api/streams",
                params={"src": name},
            )
            if resp.status_code not in (200, 204):
                logger.warning("[go2rtc] remove stream %s failed: %s", name, resp.text)
            else:
                logger.info("[go2rtc] removed stream: %s", name)
    except Exception as e:
        logger.warning("[go2rtc] could not reach go2rtc to remove stream %s: %s", name, e)


class CameraCreate(BaseModel):
    name: str
    rtsp_url: str
    location: str | None = None
    zone: str | None = None
    is_entrance: bool = False


class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    rtsp_url: str
    location: str | None
    zone: str | None
    is_entrance: bool
    status: str
    last_seen: datetime | None
    created_at: datetime
    stream_name: str


def _camera_response(cam: Camera) -> CameraResponse:
    return CameraResponse(
        id=cam.id,
        name=cam.name,
        rtsp_url=cam.rtsp_url,
        location=cam.location,
        zone=cam.zone,
        is_entrance=cam.is_entrance,
        status="online" if cam.is_active else "offline",
        last_seen=None,
        created_at=cam.created_at,
        stream_name=_stream_name(cam.name),
    )


@router.post("", response_model=CameraResponse, status_code=201)
async def create_camera(
    body: CameraCreate,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    cam = Camera(tenant_id=auth.tenant_id, **body.model_dump())
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    # Register in go2rtc (non-blocking on failure)
    await _go2rtc_add(_stream_name(cam.name), cam.rtsp_url)
    return _camera_response(cam)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(
    camera_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id,
            Camera.tenant_id == auth.tenant_id,
        )
    )
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(404, "Camera not found")
    stream_name = _stream_name(cam.name)
    # Clean up all FK references before deleting the camera row
    await db.execute(sa_delete(SessionCamera).where(SessionCamera.camera_id == camera_id))
    await db.execute(
        sa_delete(CameraPair).where(
            (CameraPair.camera_a_id == camera_id) | (CameraPair.camera_b_id == camera_id)
        )
    )
    await db.execute(sa_delete(SessionPersonTrackBinding).where(
        SessionPersonTrackBinding.camera_id == camera_id
    ))
    await db.execute(sa_delete(CameraPresenceSegment).where(
        CameraPresenceSegment.camera_id == camera_id
    ))
    # PersonEmbedding.camera_id is nullable — null it out rather than deleting the evidence
    await db.execute(
        PersonEmbedding.__table__.update()
        .where(PersonEmbedding.camera_id == camera_id)
        .values(camera_id=None)
    )
    await db.delete(cam)
    await db.commit()
    # Remove from go2rtc (non-blocking on failure)
    await _go2rtc_remove(stream_name)


@router.get("", response_model=list[CameraResponse])
async def list_cameras(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    return [_camera_response(cam) for cam in result.scalars().all()]


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(
    camera_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id,
            Camera.tenant_id == auth.tenant_id,
        )
    )
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(404, "Camera not found")
    return _camera_response(cam)
