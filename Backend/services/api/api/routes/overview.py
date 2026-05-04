"""Day-level office overview for the frontend."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.events import build_live_event_envelope
from airco.models import Alert, AttendanceEvent, Camera, Employee, PhoneEvent, Session, SessionPerson
from api.auth import AuthState, require_authenticated

router = APIRouter()

OFFICE_TZ = ZoneInfo("Asia/Kolkata")
LATE_CUTOFF = time(9, 45)
ABSENT_CUTOFF = time(11, 0)
AFTER_HOURS_START = time(7, 0)
AFTER_HOURS_END = time(20, 0)
PHONE_VIOLATION_THRESHOLD_SECONDS = 30.0
MIN_OFFICE_DT = datetime.min.replace(tzinfo=OFFICE_TZ)


def _to_office_time(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(OFFICE_TZ)


def _session_payload(session: Session) -> dict:
    return {
        "id": str(session.id),
        "name": getattr(session, "name", None),
        "status": getattr(session, "status", None),
        "mode": getattr(session, "mode", None),
        "created_at": session.created_at.isoformat() if getattr(session, "created_at", None) else None,
        "started_at": session.started_at.isoformat() if getattr(session, "started_at", None) else None,
        "stopped_at": session.stopped_at.isoformat() if getattr(session, "stopped_at", None) else None,
    }


def _session_run_start(session: Session) -> datetime | None:
    return getattr(session, "started_at", None) or getattr(session, "created_at", None)


def _session_run_end(session: Session) -> datetime | None:
    if getattr(session, "status", None) == "running":
        return None
    return getattr(session, "stopped_at", None)


def _within_session_run(
    value: datetime | None,
    *,
    session: Session,
) -> bool:
    if value is None:
        return False
    run_start = _session_run_start(session)
    run_end = _session_run_end(session)
    if run_start is not None and value < run_start:
        return False
    if run_end is not None and value > run_end:
        return False
    return True


def build_overview_payload(*, session: Session | None, summary: dict, employees: list[dict]) -> dict:
    """Build the canonical overview payload shared by REST and live publishing."""
    return {
        "session": _session_payload(session) if session is not None else None,
        "summary": summary,
        "employees": employees,
    }


def build_overview_snapshot_payload(
    *,
    tenant_id: str,
    session_id: str | None,
    occurred_at: datetime | str | None,
    overview_payload: dict,
) -> dict:
    """Build a live snapshot envelope for the overview stream."""
    return build_live_event_envelope(
        event_type="overview.snapshot",
        tenant_id=tenant_id,
        session_id=session_id,
        occurred_at=occurred_at,
        payload=overview_payload,
    )


def build_overview_patch_payload(
    *,
    tenant_id: str,
    session_id: str | None,
    occurred_at: datetime | str | None,
    patch_payload: dict,
) -> dict:
    """Build a minimal live patch envelope for the overview stream."""
    return build_live_event_envelope(
        event_type="overview.patch",
        tenant_id=tenant_id,
        session_id=session_id,
        occurred_at=occurred_at,
        payload=patch_payload,
    )


def _office_day_bounds(office_day) -> tuple[datetime, datetime]:
    office_day_start = datetime.combine(office_day, time.min, tzinfo=OFFICE_TZ)
    return office_day_start, office_day_start + timedelta(days=1)


def _office_day_time(dt: datetime | None, office_day) -> datetime | None:
    office_dt = _to_office_time(dt)
    if office_dt is None:
        return None
    office_day_start, office_day_end = _office_day_bounds(office_day)
    if office_day_start <= office_dt < office_day_end:
        return office_dt
    return None


def _session_overlaps_office_day(session: Session, office_day) -> bool:
    office_day_start, office_day_end = _office_day_bounds(office_day)
    session_start = _to_office_time(getattr(session, "started_at", None))
    session_end = _to_office_time(getattr(session, "stopped_at", None))
    if session_start is None:
        return False
    return session_start < office_day_end and (session_end is None or session_end >= office_day_start)


def _session_sort_key(session: Session) -> tuple[int, datetime, datetime, str]:
    status_priority = 1 if getattr(session, "status", None) == "running" else 0
    created_at = _to_office_time(getattr(session, "created_at", None)) or MIN_OFFICE_DT
    started_at = _to_office_time(getattr(session, "started_at", None)) or MIN_OFFICE_DT
    return (status_priority, created_at, started_at, str(session.id))


def _sessions_from_result(result) -> list[Session]:
    if hasattr(result, "scalars"):
        return [session for session in result.scalars().all() if session is not None]
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        session = scalar_one_or_none()
        return [session] if session is not None else []
    return []


def _person_arrival_time(person: SessionPerson | None, office_day) -> datetime | None:
    if person is None:
        return None
    first_seen = _office_day_time(getattr(person, "first_seen_at", None), office_day)
    if first_seen is not None:
        return first_seen
    last_seen = _office_day_time(getattr(person, "last_seen_at", None), office_day)
    if last_seen is None:
        return None
    office_day_start, _ = _office_day_bounds(office_day)
    return office_day_start


def _person_reference_key(person: SessionPerson) -> tuple[datetime, str]:
    candidates = [
        _to_office_time(value)
        for value in (
            getattr(person, "last_seen_at", None),
            getattr(person, "first_seen_at", None),
        )
        if value is not None
    ]
    return (max(candidates) if candidates else MIN_OFFICE_DT, str(person.id))


def _person_is_unknown_for_office_day(person: SessionPerson, office_day) -> bool:
    if getattr(person, "employee_id", None) is not None:
        return False
    if getattr(person, "recognition_state", None) != "unknown":
        return False
    return (
        _person_arrival_time(person, office_day) is not None
        or _office_day_time(getattr(person, "last_seen_at", None), office_day) is not None
    )


def _attendance_event_key(event: AttendanceEvent) -> tuple[datetime, str]:
    event_time = _to_office_time(event.time) or MIN_OFFICE_DT
    return (event_time, str(event.camera_id))


async def _latest_session(db: AsyncSession, tenant_id: str, office_day) -> Session | None:
    running_result = await db.execute(
        select(Session)
        .where(Session.tenant_id == tenant_id, Session.status == "running")
        .order_by(Session.created_at.desc())
    )
    running_candidates = [
        candidate
        for candidate in _sessions_from_result(running_result)
        if _session_overlaps_office_day(candidate, office_day)
    ]
    if running_candidates:
        return max(running_candidates, key=_session_sort_key)

    recent_result = await db.execute(
        select(Session)
        .where(Session.tenant_id == tenant_id)
        .order_by(Session.created_at.desc())
    )
    candidates_by_id: dict[str, Session] = {}
    for candidate in _sessions_from_result(recent_result):
        candidates_by_id.setdefault(str(candidate.id), candidate)
    valid_candidates = [
        candidate
        for candidate in candidates_by_id.values()
        if _session_overlaps_office_day(candidate, office_day)
    ]
    if valid_candidates:
        return max(valid_candidates, key=_session_sort_key)
    return None


@router.get("/today", dependencies=[Depends(require_authenticated)])
async def today_overview(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    now_local = datetime.now(OFFICE_TZ)
    office_day = now_local.date()
    session = await _latest_session(db, auth.tenant_id, office_day)
    if session is None:
        return build_overview_payload(
            session=None,
            summary={
                "counts": {
                    "expected": 0,
                    "present": 0,
                    "late": 0,
                    "absent": 0,
                    "unknown": 0,
                    "active_exceptions": 0,
                },
                "phone": {"violators": 0, "total_minutes": 0.0},
                "health": {
                    "camera_total": 0,
                    "camera_active": 0,
                    "entrance_cameras": 0,
                    "coverage_status": "healthy",
                },
            },
            employees=[],
        )

    cameras_result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    cameras = cameras_result.scalars().all()
    cameras_by_id = {str(camera.id): camera for camera in cameras}
    camera_total = len(cameras)
    camera_active = sum(1 for camera in cameras if camera.is_active)
    entrance_cameras = sum(1 for camera in cameras if camera.is_entrance)
    entrance_active = sum(1 for camera in cameras if camera.is_entrance and camera.is_active)
    inactive_total = camera_total - camera_active
    if entrance_cameras > 0 and entrance_active == 0:
        coverage_status = "critical"
    elif (entrance_cameras > 0 and entrance_active < entrance_cameras) or (
        camera_total > 0 and inactive_total / camera_total > 0.25
    ):
        coverage_status = "degraded"
    else:
        coverage_status = "healthy"

    employees_result = await db.execute(
        select(Employee)
        .where(Employee.tenant_id == auth.tenant_id, Employee.status == "active")
        .order_by(Employee.name.asc())
    )
    employees = employees_result.scalars().all()

    persons_result = await db.execute(select(SessionPerson).where(SessionPerson.session_id == session.id))
    persons = [
        person
        for person in persons_result.scalars().all()
        if _within_session_run(getattr(person, "last_seen_at", None), session=session)
    ]
    persons_by_employee_id: dict[uuid.UUID, SessionPerson] = {}
    for person in persons:
        if person.employee_id is None:
            continue
        current = persons_by_employee_id.get(person.employee_id)
        if current is None or _person_reference_key(person) > _person_reference_key(current):
            persons_by_employee_id[person.employee_id] = person
    persons_by_id = {person.id: person for person in persons}

    attendance_result = await db.execute(select(AttendanceEvent).where(AttendanceEvent.session_id == session.id))
    attendance_events = [
        event
        for event in attendance_result.scalars().all()
        if _within_session_run(getattr(event, "time", None), session=session)
    ]
    attendance_checkins_by_employee_id: dict[uuid.UUID, list[datetime]] = {}
    attendance_latest_by_employee_id: dict[uuid.UUID, AttendanceEvent] = {}
    for event in attendance_events:
        if event.event_type != "check_in":
            continue
        event_employee_id = event.employee_id
        if event_employee_id is None and event.session_person_id in persons_by_id:
            event_employee_id = persons_by_id[event.session_person_id].employee_id
        if event_employee_id is None:
            continue
        attendance_checkins_by_employee_id.setdefault(event_employee_id, []).append(event.time)
        current_event = attendance_latest_by_employee_id.get(event_employee_id)
        if current_event is None or _attendance_event_key(event) > _attendance_event_key(current_event):
            attendance_latest_by_employee_id[event_employee_id] = event

    phone_result = await db.execute(select(PhoneEvent).where(PhoneEvent.session_id == session.id))
    phone_events = [
        event
        for event in phone_result.scalars().all()
        if _within_session_run(getattr(event, "time", None), session=session)
        and (event_time := _to_office_time(getattr(event, "time", None))) is not None
        and event_time.date() == office_day
    ]
    phone_seconds_by_employee_id: dict[uuid.UUID, float] = {}
    for event in phone_events:
        event_employee_id = None
        if event.session_person_id in persons_by_id:
            event_employee_id = persons_by_id[event.session_person_id].employee_id
        if event_employee_id is None:
            continue
        phone_seconds_by_employee_id[event_employee_id] = phone_seconds_by_employee_id.get(event_employee_id, 0.0) + float(
            event.duration_seconds or 0.0
        )

    alerts_result = await db.execute(select(Alert).where(Alert.session_id == session.id, Alert.status == "active"))
    alerts = [
        alert
        for alert in alerts_result.scalars().all()
        if _within_session_run(getattr(alert, "created_at", None), session=session)
    ]
    alert_badges_by_employee_id: dict[uuid.UUID, list[str]] = {}
    for alert in alerts:
        if alert.session_person_id not in persons_by_id:
            continue
        employee_id = persons_by_id[alert.session_person_id].employee_id
        if employee_id is None:
            continue
        alert_badges_by_employee_id.setdefault(employee_id, []).append(alert.alert_type)

    employee_rows: list[dict] = []
    for employee in employees:
        person = persons_by_employee_id.get(employee.id)
        check_in_times = [
            _to_office_time(event_time)
            for event_time in attendance_checkins_by_employee_id.get(employee.id, [])
        ]
        check_in_times = [event_time for event_time in check_in_times if event_time is not None and event_time.date() == office_day]
        check_in_times.sort()
        first_check_in = check_in_times[0] if check_in_times else None
        latest_check_in = check_in_times[-1] if check_in_times else None
        person_arrival = _person_arrival_time(person, office_day)
        person_last_seen = _office_day_time(getattr(person, "last_seen_at", None), office_day) if person else None
        display_person = person if (person_arrival is not None or person_last_seen is not None) else None
        arrival_time = first_check_in or person_arrival
        last_seen = person_last_seen or latest_check_in
        active_presence = bool(person and person.is_active and (person_arrival is not None or person_last_seen is not None))

        current_camera = None
        current_zone = None
        if display_person and display_person.current_cameras:
            for camera_id in display_person.current_cameras:
                camera = cameras_by_id.get(str(camera_id))
                if camera:
                    current_camera = camera.name
                    current_zone = camera.zone
                    break
        if current_camera is None and employee.id in attendance_latest_by_employee_id:
            camera = cameras_by_id.get(str(attendance_latest_by_employee_id[employee.id].camera_id))
            if camera:
                current_camera = camera.name
                current_zone = camera.zone

        if arrival_time is not None:
            arrival_time_local = arrival_time
            if arrival_time_local.time() >= ABSENT_CUTOFF:
                attendance_status = "absent"
            elif LATE_CUTOFF < arrival_time_local.time() < ABSENT_CUTOFF:
                attendance_status = "late"
            else:
                attendance_status = "present"
        elif now_local.time() < ABSENT_CUTOFF:
            attendance_status = "unknown"
        else:
            attendance_status = "absent"

        is_present = bool(active_presence or check_in_times)
        if attendance_status == "absent":
            is_present = False

        after_hours_presence = False
        if active_presence:
            if (
                arrival_time and (
                    arrival_time.time() < AFTER_HOURS_START
                    or arrival_time.time() > AFTER_HOURS_END
                )
            ) or now_local.time() < AFTER_HOURS_START or now_local.time() > AFTER_HOURS_END:
                after_hours_presence = True
        else:
            for candidate in check_in_times:
                if candidate.time() < AFTER_HOURS_START or candidate.time() > AFTER_HOURS_END:
                    after_hours_presence = True
                    break

        phone_seconds = phone_seconds_by_employee_id.get(employee.id, 0.0)
        phone_minutes = round(phone_seconds / 60.0, 1)
        exception_badges: list[str] = []
        if attendance_status == "late":
            exception_badges.append("late")
        if attendance_status == "absent":
            exception_badges.append("absent")
        if after_hours_presence:
            exception_badges.append("after_hours")
        if phone_seconds >= PHONE_VIOLATION_THRESHOLD_SECONDS:
            exception_badges.append("phone_violation")
        exception_badges.extend(alert_badges_by_employee_id.get(employee.id, []))
        if display_person is None:
            recognition_state = "unknown"
            confidence = 0.0
        else:
            recognition_state = display_person.recognition_state
            confidence = float(display_person.face_confidence or 0.0)

        employee_rows.append(
            {
                "employee_id": str(employee.id),
                "employee_name": employee.name,
                "is_present": is_present,
                "attendance_status": attendance_status,
                "current_zone": current_zone,
                "current_camera": current_camera,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "recognition_state": recognition_state,
                "confidence": confidence,
                "phone_usage_minutes": phone_minutes,
                "exception_badges": list(dict.fromkeys(exception_badges)),
            }
        )

    unknown_count = sum(1 for person in persons if _person_is_unknown_for_office_day(person, office_day))
    present_count = sum(1 for row in employee_rows if row["is_present"])
    late_count = sum(1 for row in employee_rows if row["attendance_status"] == "late")
    absent_count = sum(1 for row in employee_rows if row["attendance_status"] == "absent")
    active_exceptions = sum(1 for row in employee_rows if row["exception_badges"])
    phone_violators = sum(
        1
        for row in employee_rows
        if any(
            badge == "phone_violation"
            for badge in row["exception_badges"]
        )
    )
    total_phone_minutes = round(
        sum(phone_seconds_by_employee_id.values()) / 60.0,
        1,
    )

    return build_overview_payload(
        session=session,
        summary={
            "counts": {
                "expected": len(employees),
                "present": present_count,
                "late": late_count,
                "absent": absent_count,
                "unknown": unknown_count,
                "active_exceptions": active_exceptions,
            },
            "phone": {
                "violators": phone_violators,
                "total_minutes": total_phone_minutes,
            },
            "health": {
                "camera_total": camera_total,
                "camera_active": camera_active,
                "entrance_cameras": entrance_cameras,
                "coverage_status": coverage_status,
            },
        },
        employees=employee_rows,
    )
