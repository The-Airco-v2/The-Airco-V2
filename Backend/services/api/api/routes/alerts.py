"""Alert CRUD."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import Alert, Camera, Snapshot
from api.auth import require_authenticated, require_admin

try:
    from api.routes.employee_intelligence import _public_asset_url
except ImportError:
    def _public_asset_url(value: str | None) -> str | None:
        return value

router = APIRouter()


class AlertResponse(BaseModel):
    id: uuid.UUID
    type: str
    severity: str
    camera_id: uuid.UUID | None
    camera_name: str | None
    session_id: uuid.UUID
    message: str
    acknowledged: bool
    created_at: datetime
    evidence_url: str | None = None
    snapshot_url: str | None = None


async def _camera_name(db: AsyncSession, camera_id: uuid.UUID | None) -> str | None:
    if camera_id is None:
        return None
    result = await db.execute(select(Camera.name).where(Camera.id == camera_id))
    scalar = getattr(result, "scalar_one_or_none", None) or getattr(result, "scalar_one", None)
    return scalar() if callable(scalar) else None


async def _best_snapshot_url(db: AsyncSession, alert: "Alert") -> str | None:
    """Find the best snapshot image for this alert via session_person_id."""
    if alert.session_person_id is None:
        return None
    stmt = (
        select(Snapshot.full_frame_url, Snapshot.face_crop_url, Snapshot.body_crop_url)
        .where(Snapshot.session_person_id == alert.session_person_id)
        .order_by(Snapshot.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return _public_asset_url(row.full_frame_url or row.body_crop_url or row.face_crop_url)


async def _alert_response(db: AsyncSession, alert: "Alert") -> AlertResponse:
    snapshot_url = await _best_snapshot_url(db, alert)
    return AlertResponse(
        id=alert.id,
        type=alert.alert_type,
        severity=alert.severity,
        camera_id=alert.camera_id,
        camera_name=await _camera_name(db, alert.camera_id),
        session_id=alert.session_id,
        message=alert.message,
        acknowledged=alert.acknowledged_at is not None,
        created_at=alert.created_at,
        evidence_url=_public_asset_url(alert.evidence_url),
        snapshot_url=snapshot_url,
    )


async def _acknowledged_alert_response(db: AsyncSession, alert: "Alert") -> AlertResponse:
    return await _alert_response(db, alert)


@router.get("", dependencies=[Depends(require_authenticated)])
async def list_alerts(
    session_id: uuid.UUID = Query(...),
    limit: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_session),
):
    query = select(Alert).where(Alert.session_id == session_id).order_by(Alert.created_at.desc())
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()
    return [await _alert_response(db, alert) for alert in alerts]


@router.post("/{alert_id}/acknowledge", dependencies=[Depends(require_admin)])
async def acknowledge_alert(alert_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.acknowledged_at is not None:
        return {"status": "acknowledged"}

    await db.execute(
        update(Alert).where(Alert.id == alert_id).values(
            status="acknowledged",
            acknowledged_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    return {"status": "acknowledged"}
