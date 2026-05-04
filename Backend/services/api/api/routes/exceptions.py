"""Near-term exceptions aggregation queue."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import Alert, AttendanceEvent, Camera, Employee, PhoneEvent, ReviewTask, Session, SessionPerson
from api.auth import AuthState, require_authenticated

router = APIRouter()

OFFICE_TZ = ZoneInfo("Asia/Kolkata")
LATE_CUTOFF = time(9, 45)
ABSENT_CUTOFF = time(11, 0)
AFTER_HOURS_START = time(7, 0)
AFTER_HOURS_END = time(20, 0)
PHONE_VIOLATION_THRESHOLD_SECONDS = 30.0
LOW_CONFIDENCE_THRESHOLD = 0.75
MIN_OFFICE_DT = datetime.min.replace(tzinfo=OFFICE_TZ)
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _to_office_time(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(OFFICE_TZ)


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


def _office_day_for_dt(dt: datetime | None):
    office_dt = _to_office_time(dt)
    if office_dt is None:
        return None
    return office_dt.date()


def _session_office_day(session: Session) -> datetime.date:
    for candidate in (
        getattr(session, "started_at", None),
        getattr(session, "created_at", None),
    ):
        office_dt = _to_office_time(candidate)
        if office_dt is not None:
            return office_dt.date()
    return datetime.now(OFFICE_TZ).date()


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


def _selected_office_day(
    session: Session,
    alerts: list[Alert],
    review_tasks: list[ReviewTask],
    phone_events: list[PhoneEvent],
    attendance_events: list[AttendanceEvent],
    persons: list[SessionPerson],
):
    candidate_days = [
        candidate_day
        for candidate_day in (
            *(_office_day_for_dt(getattr(alert, "created_at", None)) for alert in alerts),
            *(_office_day_for_dt(getattr(task, "created_at", None)) for task in review_tasks),
            *(_office_day_for_dt(getattr(event, "time", None)) for event in phone_events),
            *(_office_day_for_dt(getattr(event, "time", None)) for event in attendance_events),
            *(_office_day_for_dt(getattr(person, "first_seen_at", None)) for person in persons),
            *(_office_day_for_dt(getattr(person, "last_seen_at", None)) for person in persons),
        )
        if candidate_day is not None
    ]
    if candidate_days:
        return max(candidate_days)
    return _session_office_day(session)


def _camera_details(camera_id: uuid.UUID | str | None, cameras_by_id: dict[str, Camera]) -> tuple[str | None, str | None]:
    if camera_id is None:
        return None, None
    camera = cameras_by_id.get(str(camera_id))
    if camera is None:
        return None, None
    return camera.name, camera.zone


def _person_camera_details(person: SessionPerson | None, cameras_by_id: dict[str, Camera]) -> tuple[str | None, str | None]:
    if person is None or not getattr(person, "current_cameras", None):
        return None, None
    for camera_id in person.current_cameras:
        camera_name, zone_name = _camera_details(camera_id, cameras_by_id)
        if camera_name is not None or zone_name is not None:
            return camera_name, zone_name
    return None, None


def _person_employee(person: SessionPerson | None, employees_by_id: dict[str, Employee]) -> Employee | None:
    if person is None or getattr(person, "employee_id", None) is None:
        return None
    return employees_by_id.get(str(person.employee_id))


def _person_name(person: SessionPerson | None, employee: Employee | None) -> str | None:
    if employee is not None:
        return employee.name
    if person is not None:
        return getattr(person, "display_name", None)
    return None


def _severity_for_review(task: ReviewTask) -> str:
    if getattr(task, "task_type", "") == "conflict_review":
        return "high"
    return "medium"


def _severity_for_phone(total_duration_seconds: float) -> str:
    if total_duration_seconds >= 120.0:
        return "high"
    return "medium"


def _title_for_category(category: str) -> str:
    titles = {
        "late_arrival": "Late arrival",
        "absence": "Absence",
        "early_exit": "Early exit",
        "identity_unknown": "Unknown identity",
        "identity_low_confidence": "Low-confidence identity",
        "review_pending": "Pending review",
        "phone_violation": "Phone violation",
        "restricted_zone": "Restricted zone alert",
        "after_hours_presence": "After-hours presence",
        "monitoring_gap": "Monitoring gap",
    }
    return titles.get(category, category.replace("_", " ").title())


def _normalize_alert_category(alert: Alert) -> str:
    mapping = {
        "restricted_zone": "restricted_zone",
        "unknown_person": "identity_unknown",
        "after_hours_presence": "after_hours_presence",
        "monitoring_gap": "monitoring_gap",
        "phone_violation": "phone_violation",
    }
    return mapping.get(getattr(alert, "alert_type", ""), getattr(alert, "alert_type", "alert"))


def _recommended_action(category: str) -> str:
    actions = {
        "late_arrival": "review_arrival_timeline",
        "absence": "confirm_absence_status",
        "early_exit": "confirm_early_exit",
        "identity_unknown": "review_identity_evidence",
        "identity_low_confidence": "review_identity_evidence",
        "review_pending": "complete_review_decision",
        "phone_violation": "manager_follow_up",
        "restricted_zone": "acknowledge_or_resolve_alert",
        "after_hours_presence": "confirm_after_hours_authorization",
        "monitoring_gap": "restore_camera_coverage",
    }
    return actions.get(category, "review_exception")


def _source_for_alert_category(category: str) -> str:
    if category in {"identity_unknown", "identity_low_confidence"}:
        return "identity"
    if category == "phone_violation":
        return "behavior"
    if category == "monitoring_gap":
        return "system"
    return "alert"


def _base_item(
    *,
    item_id: str,
    source: str,
    category: str,
    severity: str,
    title: str,
    subtitle: str | None,
    employee_id: uuid.UUID | None,
    employee_name: str | None,
    confidence: float | None,
    camera: str | None,
    zone: str | None,
    created_at: datetime | None,
    status: str,
    recommended_action: str,
    audit_context: dict,
) -> dict:
    sort_created_at = _to_office_time(created_at) or MIN_OFFICE_DT
    return {
        "id": item_id,
        "source": source,
        "category": category,
        "severity": severity,
        "title": title,
        "subtitle": subtitle,
        "employee_id": str(employee_id) if employee_id is not None else None,
        "employee_name": employee_name,
        "confidence": None if confidence is None else float(confidence),
        "camera": camera,
        "zone": zone,
        "created_at": created_at.isoformat() if created_at is not None else None,
        "status": status,
        "recommended_action": recommended_action,
        "audit_context": audit_context,
        "_sort_created_at": sort_created_at,
    }


@router.get("", dependencies=[Depends(require_authenticated)])
async def list_exceptions(
    session_id: uuid.UUID = Query(...),
    employee_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    session_result = await db.execute(
        select(Session).where(Session.id == session_id, Session.tenant_id == auth.tenant_id)
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(404, "Session not found")

    employees_result = await db.execute(
        select(Employee)
        .where(Employee.tenant_id == auth.tenant_id, Employee.status == "active")
        .order_by(Employee.name.asc())
    )
    employees = employees_result.scalars().all()
    employees_by_id = {str(employee.id): employee for employee in employees}

    cameras_result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    cameras = cameras_result.scalars().all()
    cameras_by_id = {str(camera.id): camera for camera in cameras}

    persons_result = await db.execute(select(SessionPerson).where(SessionPerson.session_id == session_id))
    persons = persons_result.scalars().all()
    persons_by_id = {str(person.id): person for person in persons}
    persons_by_employee_id: dict[str, SessionPerson] = {}
    for person in persons:
        if getattr(person, "employee_id", None) is None:
            continue
        employee_key = str(person.employee_id)
        current = persons_by_employee_id.get(employee_key)
        if current is None or _person_reference_key(person) > _person_reference_key(current):
            persons_by_employee_id[employee_key] = person

    alerts_result = await db.execute(
        select(Alert).where(Alert.session_id == session_id).order_by(Alert.created_at.desc())
    )
    alerts = alerts_result.scalars().all()

    review_tasks_result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.session_id == session_id, ReviewTask.status == "pending")
        .order_by(ReviewTask.created_at.desc())
    )
    review_tasks = review_tasks_result.scalars().all()

    phone_events_result = await db.execute(select(PhoneEvent).where(PhoneEvent.session_id == session_id))
    phone_events = phone_events_result.scalars().all()

    attendance_result = await db.execute(select(AttendanceEvent).where(AttendanceEvent.session_id == session_id))
    attendance_events = attendance_result.scalars().all()

    office_day = _selected_office_day(session, alerts, review_tasks, phone_events, attendance_events, persons)
    office_day_start, _ = _office_day_bounds(office_day)
    now_local = datetime.now(OFFICE_TZ)
    absence_cutoff_passed = office_day < now_local.date() or (
        office_day == now_local.date() and now_local.time() >= ABSENT_CUTOFF
    )
    alerts = [
        alert for alert in alerts if _office_day_for_dt(getattr(alert, "created_at", None)) == office_day
    ]
    review_tasks = [
        task for task in review_tasks if _office_day_for_dt(getattr(task, "created_at", None)) == office_day
    ]

    items: list[dict] = []
    alert_backed_keys: set[tuple[str, str]] = set()

    for alert in alerts:
        normalized_category = _normalize_alert_category(alert)
        session_person_id = getattr(alert, "session_person_id", None)
        alert_status = getattr(alert, "status", "active")
        if (
            session_person_id is not None
            and alert_status == "active"
            and normalized_category in {"phone_violation", "identity_unknown"}
        ):
            alert_backed_keys.add((normalized_category, str(session_person_id)))
        person = persons_by_id.get(str(getattr(alert, "session_person_id", "")))
        employee = _person_employee(person, employees_by_id)
        employee_name = _person_name(person, employee)
        camera_name, zone_name = _camera_details(getattr(alert, "camera_id", None), cameras_by_id)
        if camera_name is None and zone_name is None:
            camera_name, zone_name = _person_camera_details(person, cameras_by_id)
        items.append(
            _base_item(
                item_id=str(alert.id),
                source=_source_for_alert_category(normalized_category),
                category=normalized_category,
                severity=getattr(alert, "severity", "medium"),
                title=_title_for_category(normalized_category),
                subtitle=getattr(alert, "message", None),
                employee_id=getattr(employee, "id", None),
                employee_name=employee_name,
                confidence=None if person is None else float(getattr(person, "face_confidence", 0.0) or 0.0),
                camera=camera_name,
                zone=zone_name,
                created_at=getattr(alert, "created_at", None),
                status=alert_status,
                recommended_action=_recommended_action(normalized_category),
                audit_context={
                    "session_id": str(session_id),
                    "alert_type": getattr(alert, "alert_type", None),
                    "session_person_id": str(alert.session_person_id) if getattr(alert, "session_person_id", None) else None,
                    "camera_id": str(alert.camera_id) if getattr(alert, "camera_id", None) else None,
                    "evidence_url": getattr(alert, "evidence_url", None),
                    "dedup_key": getattr(alert, "dedup_key", None),
                },
            )
        )

    for task in review_tasks:
        person = persons_by_id.get(str(task.session_person_id))
        employee = _person_employee(person, employees_by_id)
        camera_name, zone_name = _person_camera_details(person, cameras_by_id)
        items.append(
            _base_item(
                item_id=str(task.id),
                source="review",
                category="review_pending",
                severity=_severity_for_review(task),
                title=_title_for_category("review_pending"),
                subtitle=getattr(task, "task_type", None),
                employee_id=getattr(employee, "id", None),
                employee_name=_person_name(person, employee),
                confidence=None if person is None else float(getattr(person, "face_confidence", 0.0) or 0.0),
                camera=camera_name,
                zone=zone_name,
                created_at=getattr(task, "created_at", None),
                status=getattr(task, "status", "pending"),
                recommended_action=_recommended_action("review_pending"),
                audit_context={
                    "session_id": str(session_id),
                    "task_type": getattr(task, "task_type", None),
                    "session_person_id": str(task.session_person_id),
                    "evidence": getattr(task, "evidence", None),
                    "decision": getattr(task, "decision", None),
                },
            )
        )

    phone_groups: dict[str, dict] = {}
    for event in phone_events:
        event_time = _office_day_time(getattr(event, "time", None), office_day)
        if event_time is None:
            continue
        person = persons_by_id.get(str(getattr(event, "session_person_id", "")))
        group_key = str(getattr(event, "session_person_id", None) or getattr(event, "camera_id", None))
        existing_group = phone_groups.get(group_key)
        if existing_group is None:
            phone_groups[group_key] = {
                "person": person,
                "camera_id": getattr(event, "camera_id", None),
                "latest_time": event_time,
                "total_duration_seconds": float(getattr(event, "duration_seconds", 0.0) or 0.0),
                "max_confidence": float(getattr(event, "confidence", 0.0) or 0.0),
                "track_ids": [getattr(event, "track_id", None)],
            }
            continue
        existing_group["latest_time"] = max(existing_group["latest_time"], event_time)
        existing_group["total_duration_seconds"] += float(getattr(event, "duration_seconds", 0.0) or 0.0)
        existing_group["max_confidence"] = max(
            existing_group["max_confidence"],
            float(getattr(event, "confidence", 0.0) or 0.0),
        )
        existing_group["track_ids"].append(getattr(event, "track_id", None))

    for group_key, phone_group in phone_groups.items():
        if phone_group["total_duration_seconds"] < PHONE_VIOLATION_THRESHOLD_SECONDS:
            continue
        person = phone_group["person"]
        if person is not None and ("phone_violation", str(person.id)) in alert_backed_keys:
            continue
        employee = _person_employee(person, employees_by_id)
        camera_name, zone_name = _camera_details(phone_group["camera_id"], cameras_by_id)
        if camera_name is None and zone_name is None:
            camera_name, zone_name = _person_camera_details(person, cameras_by_id)
        items.append(
            _base_item(
                item_id=f"phone:{group_key}",
                source="behavior",
                category="phone_violation",
                severity=_severity_for_phone(phone_group["total_duration_seconds"]),
                title=_title_for_category("phone_violation"),
                subtitle=f"Observed for {round(phone_group['total_duration_seconds'], 1)} seconds",
                employee_id=getattr(employee, "id", None),
                employee_name=_person_name(person, employee),
                confidence=phone_group["max_confidence"],
                camera=camera_name,
                zone=zone_name,
                created_at=phone_group["latest_time"],
                status="active",
                recommended_action=_recommended_action("phone_violation"),
                audit_context={
                    "session_id": str(session_id),
                    "session_person_id": None if person is None else str(person.id),
                    "camera_id": str(phone_group["camera_id"]) if phone_group["camera_id"] else None,
                    "total_duration_seconds": round(phone_group["total_duration_seconds"], 1),
                    "track_ids": [track_id for track_id in phone_group["track_ids"] if track_id is not None],
                },
            )
        )

    for person in persons:
        latest_seen = _office_day_time(getattr(person, "last_seen_at", None), office_day)
        first_seen = _office_day_time(getattr(person, "first_seen_at", None), office_day)
        created_at = latest_seen or first_seen
        if created_at is None:
            continue
        recognition_state = getattr(person, "recognition_state", None)
        face_confidence = float(getattr(person, "face_confidence", 0.0) or 0.0)
        if recognition_state == "unknown":
            normalized_category = "identity_unknown"
            severity = "high"
        elif recognition_state == "candidate" or face_confidence < LOW_CONFIDENCE_THRESHOLD or getattr(person, "identity_conflict", False):
            normalized_category = "identity_low_confidence"
            severity = "medium"
        else:
            continue
        if normalized_category == "identity_unknown" and (normalized_category, str(person.id)) in alert_backed_keys:
            continue
        employee = _person_employee(person, employees_by_id)
        camera_name, zone_name = _person_camera_details(person, cameras_by_id)
        items.append(
            _base_item(
                item_id=str(person.id),
                source="identity",
                category=normalized_category,
                severity=severity,
                title=_title_for_category(normalized_category),
                subtitle=getattr(person, "display_name", None),
                employee_id=getattr(employee, "id", None),
                employee_name=_person_name(person, employee),
                confidence=face_confidence,
                camera=camera_name,
                zone=zone_name,
                created_at=created_at,
                status="active" if getattr(person, "is_active", False) else "resolved",
                recommended_action=_recommended_action(normalized_category),
                audit_context={
                    "session_id": str(session_id),
                    "session_person_id": str(person.id),
                    "recognition_state": recognition_state,
                    "identity_conflict": bool(getattr(person, "identity_conflict", False)),
                    "evidence_summary": getattr(person, "evidence_summary", None),
                },
            )
        )

    attendance_by_employee_id: dict[str, dict[str, list[AttendanceEvent]]] = {}
    for event in attendance_events:
        employee_key = str(getattr(event, "employee_id", None)) if getattr(event, "employee_id", None) else None
        if employee_key is None and getattr(event, "session_person_id", None) is not None:
            person = persons_by_id.get(str(event.session_person_id))
            if person is not None and getattr(person, "employee_id", None) is not None:
                employee_key = str(person.employee_id)
        if employee_key is None:
            continue
        event_time = _office_day_time(getattr(event, "time", None), office_day)
        if event_time is None:
            continue
        attendance_by_employee_id.setdefault(employee_key, {"check_in": [], "check_out": []})
        attendance_by_employee_id[employee_key].setdefault(getattr(event, "event_type", "check_in"), []).append(event)

    for employee in employees:
        employee_key = str(employee.id)
        person = persons_by_employee_id.get(employee_key)
        person_first_seen = _office_day_time(getattr(person, "first_seen_at", None), office_day) if person else None
        person_last_seen = _office_day_time(getattr(person, "last_seen_at", None), office_day) if person else None
        active_presence = bool(person and getattr(person, "is_active", False) and (person_first_seen or person_last_seen))
        attendance_data = attendance_by_employee_id.get(employee_key, {})
        check_ins = sorted(
            attendance_data.get("check_in", []),
            key=lambda event: _to_office_time(getattr(event, "time", None)) or MIN_OFFICE_DT,
        )
        check_outs = sorted(
            attendance_data.get("check_out", []),
            key=lambda event: _to_office_time(getattr(event, "time", None)) or MIN_OFFICE_DT,
        )
        first_check_in = check_ins[0] if check_ins else None
        latest_check_out = check_outs[-1] if check_outs else None
        arrival_time = _office_day_time(getattr(first_check_in, "time", None), office_day) or person_first_seen
        last_seen_time = person_last_seen or _office_day_time(getattr(latest_check_out, "time", None), office_day)
        if arrival_time is None and active_presence and person_last_seen is not None:
            arrival_time = office_day_start

        if arrival_time is None and absence_cutoff_passed:
            items.append(
                _base_item(
                    item_id=f"absence:{employee_key}:{office_day.isoformat()}",
                    source="attendance",
                    category="absence",
                    severity="high",
                    title=_title_for_category("absence"),
                    subtitle=f"No check-in recorded by {ABSENT_CUTOFF.strftime('%H:%M')}",
                    employee_id=employee.id,
                    employee_name=employee.name,
                    confidence=None,
                    camera=None,
                    zone=None,
                    created_at=datetime.combine(office_day, ABSENT_CUTOFF, tzinfo=OFFICE_TZ),
                    status="active",
                    recommended_action=_recommended_action("absence"),
                    audit_context={
                        "session_id": str(session_id),
                        "employee_id": employee_key,
                        "office_day": office_day.isoformat(),
                    },
                )
            )
            continue

        if arrival_time is not None:
            if arrival_time.time() >= ABSENT_CUTOFF:
                attendance_category = "absence"
                attendance_severity = "high"
            elif arrival_time.time() > LATE_CUTOFF:
                attendance_category = "late_arrival"
                attendance_severity = "medium"
            else:
                attendance_category = None
                attendance_severity = None

            if attendance_category is not None:
                camera_name, zone_name = _camera_details(getattr(first_check_in, "camera_id", None), cameras_by_id)
                if camera_name is None and zone_name is None:
                    camera_name, zone_name = _person_camera_details(person, cameras_by_id)
                attendance_confidence = getattr(first_check_in, "confidence", None)
                if attendance_confidence is None and person is not None:
                    attendance_confidence = getattr(person, "face_confidence", None)
                items.append(
                    _base_item(
                        item_id=f"{attendance_category}:{employee_key}:{office_day.isoformat()}",
                        source="attendance",
                        category=attendance_category,
                        severity=attendance_severity,
                        title=_title_for_category(attendance_category),
                        subtitle=f"First seen at {arrival_time.strftime('%H:%M')}",
                        employee_id=employee.id,
                        employee_name=employee.name,
                        confidence=attendance_confidence,
                        camera=camera_name,
                        zone=zone_name,
                        created_at=arrival_time,
                        status="active",
                        recommended_action=_recommended_action(attendance_category),
                        audit_context={
                            "session_id": str(session_id),
                            "employee_id": employee_key,
                            "first_check_in_camera_id": str(first_check_in.camera_id) if first_check_in else None,
                            "office_day": office_day.isoformat(),
                        },
                    )
                )

        if latest_check_out is not None and not active_presence:
            check_out_time = _office_day_time(getattr(latest_check_out, "time", None), office_day)
            if check_out_time is not None and check_out_time.time() < AFTER_HOURS_END:
                camera_name, zone_name = _camera_details(getattr(latest_check_out, "camera_id", None), cameras_by_id)
                items.append(
                    _base_item(
                        item_id=f"early_exit:{employee_key}:{office_day.isoformat()}",
                        source="attendance",
                        category="early_exit",
                        severity="medium",
                        title=_title_for_category("early_exit"),
                        subtitle=f"Checked out at {check_out_time.strftime('%H:%M')}",
                        employee_id=employee.id,
                        employee_name=employee.name,
                        confidence=getattr(latest_check_out, "confidence", None),
                        camera=camera_name,
                        zone=zone_name,
                        created_at=check_out_time,
                        status="active",
                        recommended_action=_recommended_action("early_exit"),
                        audit_context={
                            "session_id": str(session_id),
                            "employee_id": employee_key,
                            "office_day": office_day.isoformat(),
                        },
                    )
                )

        after_hours_candidates = [candidate for candidate in (arrival_time, last_seen_time) if candidate is not None]
        if active_presence and office_day == now_local.date() and (
            now_local.time() < AFTER_HOURS_START or now_local.time() > AFTER_HOURS_END
        ):
            after_hours_candidates.append(now_local)
        if any(
            candidate.time() < AFTER_HOURS_START or candidate.time() > AFTER_HOURS_END
            for candidate in after_hours_candidates
        ):
            created_at = max(after_hours_candidates) if after_hours_candidates else None
            camera_name, zone_name = _person_camera_details(person, cameras_by_id)
            items.append(
                _base_item(
                    item_id=f"after_hours:{employee_key}:{office_day.isoformat()}",
                    source="attendance",
                    category="after_hours_presence",
                    severity="medium",
                    title=_title_for_category("after_hours_presence"),
                    subtitle="Observed outside standard office hours",
                    employee_id=employee.id,
                    employee_name=employee.name,
                    confidence=None if person is None else getattr(person, "face_confidence", None),
                    camera=camera_name,
                    zone=zone_name,
                    created_at=created_at,
                    status="active",
                    recommended_action=_recommended_action("after_hours_presence"),
                    audit_context={
                        "session_id": str(session_id),
                        "employee_id": employee_key,
                        "office_day": office_day.isoformat(),
                    },
                )
            )

    camera_total = len(cameras)
    camera_active = sum(1 for camera in cameras if getattr(camera, "is_active", False))
    entrance_total = sum(1 for camera in cameras if getattr(camera, "is_entrance", False))
    entrance_active = sum(
        1 for camera in cameras if getattr(camera, "is_entrance", False) and getattr(camera, "is_active", False)
    )
    monitoring_gap = False
    monitoring_severity = "medium"
    monitoring_subtitle = None
    if entrance_total > 0 and entrance_active == 0:
        monitoring_gap = True
        monitoring_severity = "critical"
        monitoring_subtitle = "No entrance cameras are currently active"
    elif entrance_total > 0 and entrance_active < entrance_total:
        monitoring_gap = True
        monitoring_severity = "high"
        monitoring_subtitle = "Entrance camera coverage is degraded"
    elif camera_total > 0 and (camera_total - camera_active) / camera_total > 0.25:
        monitoring_gap = True
        monitoring_severity = "medium"
        monitoring_subtitle = "Overall camera coverage is degraded"

    if monitoring_gap:
        items.append(
            _base_item(
                item_id=f"monitoring:{session_id}:{office_day.isoformat()}",
                source="system",
                category="monitoring_gap",
                severity=monitoring_severity,
                title=_title_for_category("monitoring_gap"),
                subtitle=monitoring_subtitle,
                employee_id=None,
                employee_name=None,
                confidence=None,
                camera=None,
                zone=None,
                created_at=_to_office_time(getattr(session, "created_at", None)) or office_day_start,
                status="active",
                recommended_action=_recommended_action("monitoring_gap"),
                audit_context={
                    "session_id": str(session_id),
                    "camera_total": camera_total,
                    "camera_active": camera_active,
                    "entrance_total": entrance_total,
                    "entrance_active": entrance_active,
                },
            )
        )

    if employee_id is not None:
        employee_key = str(employee_id)
        items = [item for item in items if item["employee_id"] == employee_key]
    if category is not None:
        items = [item for item in items if item["category"] == category]
    if status is not None:
        items = [item for item in items if item["status"] == status]

    items.sort(
        key=lambda item: (
            item["_sort_created_at"],
            SEVERITY_RANK.get(item["severity"], 0),
            item["id"],
        ),
        reverse=True,
    )
    for item in items:
        item.pop("_sort_created_at", None)
    return items
