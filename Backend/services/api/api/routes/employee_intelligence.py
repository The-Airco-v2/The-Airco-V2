"""Employee Intelligence endpoint — THE client contract.

GET /api/v2/sessions/{session_id}/employee-intelligence
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.minio_client import get_presigned_url
from api.auth import require_authenticated
from airco.models import (
    SessionPerson, Employee, CameraPresenceSegment, Camera,
    PhoneEvent, ActivityEvent, Alert,
)

router = APIRouter()


class PresenceInfo(BaseModel):
    is_present: bool
    entered_at: str | None = None
    last_seen: str | None = None


class LocationInfo(BaseModel):
    current_zone: str | None = None
    current_camera: str | None = None


class ProductivityInfo(BaseModel):
    working_seconds: float = 0
    idle_seconds: float = 0
    productivity_percent: int = 0


class ViolationInfo(BaseModel):
    phone_usage_minutes: float = 0
    phone_violation: bool = False
    restricted_zone_violation: bool = False


class EmployeeIntelligence(BaseModel):
    employee_id: str | None
    employee_name: str
    training_status: str
    presence: PresenceInfo
    live_status: str
    location: LocationInfo
    movement_path: list[dict]
    productivity: ProductivityInfo
    dwell_analysis: dict[str, float]
    violations: ViolationInfo
    recognition_state: str
    best_thumbnail_url: str | None
    confidence: float


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_asset_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if "://" in normalized or normalized.startswith("data:") or normalized.startswith("blob:"):
        return normalized
    try:
        return get_presigned_url(normalized.lstrip("/"))
    except Exception:
        return normalized


def _dwell_key(camera_name: str, camera_id: uuid.UUID, dwell_analysis: dict[str, float]) -> str:
    if camera_name not in dwell_analysis:
        return camera_name
    return f"{camera_name} ({camera_id})"


def _is_employee_person(person: SessionPerson) -> bool:
    return getattr(person, "employee_id", None) is not None


async def _employee_intelligence_response(
    db: AsyncSession,
    person: SessionPerson,
) -> EmployeeIntelligence:
    emp_name = person.display_name
    training_status = "untrained"
    if person.employee_id:
        emp_result = await db.execute(select(Employee).where(Employee.id == person.employee_id))
        emp = emp_result.scalar_one_or_none()
        if emp:
            emp_name = emp.name
            training_status = "trained"

    dwell_result = await db.execute(
        select(
            CameraPresenceSegment.camera_id,
            func.sum(CameraPresenceSegment.dwell_seconds).label("total"),
        ).where(
            CameraPresenceSegment.session_person_id == person.id
        ).group_by(CameraPresenceSegment.camera_id).order_by(CameraPresenceSegment.camera_id)
    )
    dwell_analysis = {}
    for row in dwell_result:
        cam_result = await db.execute(select(Camera.name).where(Camera.id == row.camera_id))
        cam_name = cam_result.scalar_one_or_none() or str(row.camera_id)
        dwell_analysis[_dwell_key(cam_name, row.camera_id, dwell_analysis)] = float(row.total or 0)

    path_result = await db.execute(
        select(CameraPresenceSegment, Camera.zone).join(
            Camera, CameraPresenceSegment.camera_id == Camera.id
        ).where(
            CameraPresenceSegment.session_person_id == person.id
        ).order_by(CameraPresenceSegment.entered_at)
    )
    movement_path = []
    for seg, zone in path_result:
        movement_path.append({
            "zone": zone or "Unknown",
            "time": seg.entered_at.strftime("%H:%M:%S") if seg.entered_at else "",
        })

    phone_result = await db.execute(
        select(func.sum(PhoneEvent.duration_seconds)).where(
            PhoneEvent.session_person_id == person.id
        )
    )
    phone_seconds = float(phone_result.scalar_one_or_none() or 0)

    activity_result = await db.execute(
        select(ActivityEvent.activity, func.count()).where(
            ActivityEvent.session_person_id == person.id
        ).group_by(ActivityEvent.activity)
    )
    working = idle = 0.0
    for act, count in activity_result:
        if act == "working":
            working = float(count)
        elif act == "idle":
            idle = float(count)

    total_activity = working + idle
    productivity_pct = int((working / total_activity * 100) if total_activity > 0 else 0)

    latest_activity = await db.execute(
        select(ActivityEvent.activity).where(
            ActivityEvent.session_person_id == person.id
        ).order_by(ActivityEvent.time.desc()).limit(1)
    )
    live_status = latest_activity.scalar_one_or_none() or "idle"

    rz_result = await db.execute(
        select(func.count()).select_from(Alert).where(
            Alert.session_person_id == person.id,
            Alert.alert_type == "restricted_zone",
        )
    )
    has_restricted_violation = (rz_result.scalar_one_or_none() or 0) > 0

    current_cam = None
    current_zone = None
    current_camera_ids = sorted({camera_id for camera_id in (person.current_cameras or [])})
    for current_camera_id in current_camera_ids:
        try:
            cam_r = await db.execute(select(Camera).where(Camera.id == uuid.UUID(current_camera_id)))
        except ValueError:
            continue
        cam_obj = cam_r.scalar_one_or_none()
        if cam_obj:
            current_cam = cam_obj.name
            current_zone = cam_obj.zone
            break

    if current_cam is None and current_camera_ids:
        current_cam = current_camera_ids[0]

    return EmployeeIntelligence(
        employee_id=str(person.employee_id) if person.employee_id else None,
        employee_name=emp_name,
        training_status=training_status,
        presence=PresenceInfo(
            is_present=person.is_active,
            entered_at=_utc_text(person.first_seen_at),
            last_seen=_utc_text(person.last_seen_at),
        ),
        live_status=live_status,
        location=LocationInfo(current_zone=current_zone, current_camera=current_cam),
        movement_path=movement_path,
        productivity=ProductivityInfo(
            working_seconds=working,
            idle_seconds=idle,
            productivity_percent=productivity_pct,
        ),
        dwell_analysis=dwell_analysis,
        violations=ViolationInfo(
            phone_usage_minutes=round(phone_seconds / 60, 1),
            phone_violation=phone_seconds > 30,
            restricted_zone_violation=has_restricted_violation,
        ),
        recognition_state=person.recognition_state,
        best_thumbnail_url=_public_asset_url(person.best_thumbnail_url),
        confidence=person.face_confidence or 0.0,
    )


@router.get("/sessions/{session_id}/employee-intelligence", dependencies=[Depends(require_authenticated)])
async def get_employee_intelligence(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(SessionPerson).where(
            SessionPerson.session_id == session_id,
            SessionPerson.employee_id.is_not(None),
        )
    )
    persons = [person for person in result.scalars().all() if _is_employee_person(person)]

    employees_data = [await _employee_intelligence_response(db, person) for person in persons]

    return {"employees": employees_data}
