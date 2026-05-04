"""Reports endpoints — report-oriented daily and historical aggregates."""

from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, time, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import (
    ActivityEvent,
    Alert,
    AttendanceEvent,
    Camera,
    Employee,
    PhoneEvent,
    Session,
    SessionPerson,
)
from api.auth import AuthState, require_authenticated
from api.routes import overview as overview_route

router = APIRouter()


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _session_stub(session: Session | None) -> dict | None:
    if session is None:
        return None
    return {
        "id": str(session.id),
        "name": getattr(session, "name", None),
        "status": getattr(session, "status", None),
    }


def _scalar_result_value(result):
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        items = scalars().all()
        if not items:
            return None
        return items[0]
    return None


def _office_day_label(office_day: date) -> str:
    return f"{calendar.month_abbr[office_day.month]} {office_day.day}, {office_day.year}"


def _office_day_bounds_utc(office_day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(office_day, time.min, tzinfo=overview_route.OFFICE_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def _active_employees(auth: AuthState, db: AsyncSession) -> list[Employee]:
    employees_result = await db.execute(
        select(Employee)
        .where(Employee.tenant_id == auth.tenant_id, Employee.status == "active")
        .order_by(Employee.name.asc())
    )
    return employees_result.scalars().all()


async def _active_camera_counts(auth: AuthState, db: AsyncSession) -> tuple[int, int]:
    cameras_result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    cameras = cameras_result.scalars().all()
    return sum(1 for camera in cameras if getattr(camera, "is_active", False)), len(cameras)


async def _sessions_for_office_day(
    office_day: date,
    auth: AuthState,
    db: AsyncSession,
) -> list[Session]:
    start_utc, end_utc = _office_day_bounds_utc(office_day)
    sessions_result = await db.execute(
        select(Session)
        .where(
            Session.tenant_id == auth.tenant_id,
            Session.created_at >= start_utc,
            Session.created_at < end_utc,
        )
        .order_by(Session.created_at.asc())
    )
    return sessions_result.scalars().all()


def _row_status_for_first_entry(first_entry: datetime | None) -> str:
    if first_entry is None:
        return "absent"
    if overview_route.LATE_CUTOFF < first_entry.time() < overview_route.ABSENT_CUTOFF:
        return "late"
    return "on_time"


async def _employee_day_rows(
    office_day: date,
    auth: AuthState,
    db: AsyncSession,
    employees: list[Employee] | None = None,
    sessions: list[Session] | None = None,
) -> tuple[list[dict], list[uuid.UUID]]:
    employees = employees if employees is not None else await _active_employees(auth, db)
    sessions = sessions if sessions is not None else await _sessions_for_office_day(office_day, auth, db)
    session_ids = [session.id for session in sessions]

    first_check_in_by_employee_id: dict[uuid.UUID, datetime] = {}
    if session_ids:
        attendance_result = await db.execute(
            select(AttendanceEvent).where(
                AttendanceEvent.session_id.in_(session_ids),
                AttendanceEvent.event_type == "check_in",
            )
        )
        attendance_events = attendance_result.scalars().all()
        for event in attendance_events:
            employee_id = getattr(event, "employee_id", None)
            event_time = overview_route._to_office_time(getattr(event, "time", None))
            if employee_id is None or event_time is None or event_time.date() != office_day:
                continue
            current = first_check_in_by_employee_id.get(employee_id)
            if current is None or event_time < current:
                first_check_in_by_employee_id[employee_id] = event_time

    rows: list[dict] = []
    for employee in employees:
        person_ids: list[uuid.UUID] = []
        last_seen: datetime | None = None
        total_work_minutes = 0

        if session_ids:
            person_result = await db.execute(
                select(SessionPerson)
                .where(
                    SessionPerson.session_id.in_(session_ids),
                    SessionPerson.employee_id == employee.id,
                )
                .order_by(SessionPerson.last_seen_at.desc())
            )
            persons = person_result.scalars().all()
        else:
            persons = []

        for person in persons:
            person_id = getattr(person, "id", getattr(person, "session_person_id", None))
            if person_id is not None:
                person_ids.append(person_id)
            first_seen_at = getattr(person, "first_seen_at", None)
            person_last_seen = getattr(person, "last_seen_at", None)
            if person_last_seen and (last_seen is None or person_last_seen > last_seen):
                last_seen = person_last_seen
            if first_seen_at and person_last_seen:
                total_work_minutes += int(
                    round((person_last_seen - first_seen_at).total_seconds() / 60.0)
                )

        if person_ids:
            productivity_result = await db.execute(
                select(func.avg(ActivityEvent.confidence)).where(
                    ActivityEvent.session_person_id.in_(person_ids)
                )
            )
            productivity_percent = int(round(float(_scalar_result_value(productivity_result) or 0)))

            violations_result = await db.execute(
                select(func.count()).select_from(Alert).where(
                    Alert.session_person_id.in_(person_ids)
                )
            )
            violations = int(_scalar_result_value(violations_result) or 0)
        else:
            productivity_percent = 0
            violations = 0

        first_entry = first_check_in_by_employee_id.get(employee.id)
        rows.append(
            {
                "employee_id": str(employee.id),
                "employee_name": employee.name,
                "department": getattr(employee, "department", None),
                "first_entry": _utc_text(first_entry.astimezone(timezone.utc) if first_entry else None),
                "last_seen": _utc_text(last_seen),
                "total_work_minutes": total_work_minutes,
                "status": _row_status_for_first_entry(first_entry),
                "violations": violations,
                "productivity_percent": productivity_percent,
            }
        )

    return rows, session_ids


async def _day_summary_payload(
    office_day: date,
    auth: AuthState,
    db: AsyncSession,
    *,
    include_empty: bool = True,
):
    employees = await _active_employees(auth, db)
    sessions = await _sessions_for_office_day(office_day, auth, db)
    if not sessions and not include_empty:
        return None

    rows, session_ids = await _employee_day_rows(office_day, auth, db, employees=employees, sessions=sessions)
    online_cameras, _ = await _active_camera_counts(auth, db)

    footfall = 0
    if session_ids:
        footfall_result = await db.execute(
            select(func.count())
            .select_from(AttendanceEvent)
            .where(
                AttendanceEvent.session_id.in_(session_ids),
                AttendanceEvent.event_type == "check_in",
            )
        )
        footfall = int(_scalar_result_value(footfall_result) or 0)

    present_rows = [row for row in rows if row["status"] != "absent"]
    avg_productivity = int(
        round(sum(row["productivity_percent"] for row in present_rows) / len(present_rows))
    ) if present_rows else 0

    return {
        "date": office_day.isoformat(),
        "label": _office_day_label(office_day),
        "summary": {
            "present": len(present_rows),
            "absent": max(len(employees) - len(present_rows), 0),
            "late_arrivals": sum(1 for row in rows if row["status"] == "late"),
            "avg_productivity": avg_productivity,
            "active_cameras": online_cameras,
            "footfall": footfall,
            "violations": sum(row["violations"] for row in rows),
        },
        "employees": rows,
    }


async def _monthly_timeline_payload(days: int, auth: AuthState, db: AsyncSession):
    office_today = datetime.now(overview_route.OFFICE_TZ).date()
    items: list[dict] = []
    for offset in range(1, days + 1):
        office_day = office_today - timedelta(days=offset)
        payload = await _day_summary_payload(office_day, auth, db, include_empty=False)
        if payload is None:
            continue
        items.append(
            {
                "date": payload["date"],
                "label": payload["label"],
                "summary": payload["summary"],
            }
        )
    return {"days": items}


async def _employee_analysis_payload(days: int, auth: AuthState, db: AsyncSession):
    timeline = await _monthly_timeline_payload(days, auth, db)
    employee_map: dict[str, dict] = {}
    total_days = len(timeline["days"])

    for day in timeline["days"]:
        payload = await _day_summary_payload(date.fromisoformat(day["date"]), auth, db, include_empty=False)
        if payload is None:
            continue
        for row in payload["employees"]:
            entry = employee_map.setdefault(
                row["employee_id"],
                {
                    "employee_id": row["employee_id"],
                    "employee_name": row["employee_name"],
                    "department": row["department"],
                    "productivity_sum": 0,
                    "productivity_count": 0,
                    "late_count": 0,
                    "work_minutes_sum": 0,
                    "work_minutes_count": 0,
                    "days_present": 0,
                    "days_absent": 0,
                    "violations": 0,
                },
            )
            if row["status"] == "absent":
                entry["days_absent"] += 1
            else:
                entry["days_present"] += 1
                entry["productivity_sum"] += row["productivity_percent"]
                entry["productivity_count"] += 1
                entry["work_minutes_sum"] += row["total_work_minutes"]
                entry["work_minutes_count"] += 1
            if row["status"] == "late":
                entry["late_count"] += 1
            entry["violations"] += row["violations"]

    employees = []
    for entry in employee_map.values():
        avg_productivity = int(
            round(entry["productivity_sum"] / entry["productivity_count"])
        ) if entry["productivity_count"] else 0
        avg_work_hours = round(
            (entry["work_minutes_sum"] / 60.0) / entry["work_minutes_count"],
            1,
        ) if entry["work_minutes_count"] else 0.0
        employees.append(
            {
                "employee_id": entry["employee_id"],
                "employee_name": entry["employee_name"],
                "department": entry["department"],
                "avg_productivity_percent": avg_productivity,
                "late_count": entry["late_count"],
                "avg_work_hours": avg_work_hours,
                "days_present": entry["days_present"],
                "days_absent": entry["days_absent"] if total_days else 0,
                "violations": entry["violations"],
            }
        )

    employees.sort(
        key=lambda item: (-item["avg_productivity_percent"], item["late_count"], item["employee_name"])
    )
    return {"days": days, "employees": employees}


async def _leaderboards_payload(days: int, auth: AuthState, db: AsyncSession):
    analysis = await _employee_analysis_payload(days, auth, db)
    employees = analysis["employees"]
    total_days = max(len((await _monthly_timeline_payload(days, auth, db))["days"]), 1)

    performers = sorted(
        (
            {
                "employee_name": employee["employee_name"],
                "score": int(
                    round(employee["avg_productivity_percent"] * 0.7 + (employee["days_present"] / total_days * 100) * 0.3)
                ),
            }
            for employee in employees
        ),
        key=lambda item: (-item["score"], item["employee_name"]),
    )[:5]

    attendance = sorted(
        (
            {
                "employee_name": employee["employee_name"],
                "attendance_percent": int(round(employee["days_present"] / total_days * 100)),
            }
            for employee in employees
        ),
        key=lambda item: (-item["attendance_percent"], item["employee_name"]),
    )[:5]

    late_behavior = sorted(
        (
            {
                "employee_name": employee["employee_name"],
                "late_count": employee["late_count"],
            }
            for employee in employees
        ),
        key=lambda item: (-item["late_count"], item["employee_name"]),
    )[:5]

    low_work_hours = sorted(
        (
            {
                "employee_name": employee["employee_name"],
                "avg_work_hours": employee["avg_work_hours"],
            }
            for employee in employees
        ),
        key=lambda item: (item["avg_work_hours"], item["employee_name"]),
    )[:5]

    for index, item in enumerate(performers, start=1):
        item["rank"] = index
    for index, item in enumerate(attendance, start=1):
        item["rank"] = index
    for index, item in enumerate(late_behavior, start=1):
        item["rank"] = index
    for index, item in enumerate(low_work_hours, start=1):
        item["rank"] = index

    return {
        "days": days,
        "performers": performers,
        "attendance": attendance,
        "late_behavior": late_behavior,
        "low_work_hours": low_work_hours,
    }


async def _today_session(auth: AuthState, db: AsyncSession) -> Session | None:
    office_day = datetime.now(overview_route.OFFICE_TZ).date()
    return await overview_route._latest_session(db, auth.tenant_id, office_day)


async def _report_productivity_average(session_id: uuid.UUID, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.avg(ActivityEvent.confidence)).where(ActivityEvent.session_id == session_id)
    )
    value = _scalar_result_value(result)
    return int(round(float(value or 0)))


async def _report_footfall_count(session_id: uuid.UUID, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(AttendanceEvent)
        .where(AttendanceEvent.session_id == session_id, AttendanceEvent.event_type == "check_in")
    )
    return int(_scalar_result_value(result) or 0)


@router.get("/today-summary", dependencies=[Depends(require_authenticated)])
async def today_summary(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    session = await _today_session(auth, db)
    if session is None:
        return {
            "session": None,
            "cards": {
                "present_today": 0,
                "absent_today": 0,
                "late_arrivals": 0,
                "avg_productivity": 0,
                "active_cameras": {"online": 0, "total": 0},
                "footfall_today": 0,
            },
        }

    cameras_result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    cameras = cameras_result.scalars().all()

    employees_result = await db.execute(
        select(Employee)
        .where(Employee.tenant_id == auth.tenant_id, Employee.status == "active")
        .order_by(Employee.name.asc())
    )
    employees = employees_result.scalars().all()

    attendance_result = await db.execute(
        select(AttendanceEvent).where(
            AttendanceEvent.session_id == session.id,
            AttendanceEvent.event_type == "check_in",
        )
    )
    attendance_events = attendance_result.scalars().all()

    first_check_in_by_employee_id: dict[uuid.UUID, datetime] = {}
    office_day = datetime.now(overview_route.OFFICE_TZ).date()
    for event in attendance_events:
        employee_id = getattr(event, "employee_id", None)
        event_time = overview_route._to_office_time(getattr(event, "time", None))
        if employee_id is None or event_time is None or event_time.date() != office_day:
            continue
        current = first_check_in_by_employee_id.get(employee_id)
        if current is None or event_time < current:
            first_check_in_by_employee_id[employee_id] = event_time

    late_arrivals = sum(
        1
        for first_seen in first_check_in_by_employee_id.values()
        if overview_route.LATE_CUTOFF < first_seen.time() < overview_route.ABSENT_CUTOFF
    )
    present_today = len(first_check_in_by_employee_id)
    absent_today = max(len(employees) - present_today, 0)

    return {
        "session": _session_stub(session),
        "cards": {
            "present_today": present_today,
            "absent_today": absent_today,
            "late_arrivals": late_arrivals,
            "avg_productivity": await _report_productivity_average(session.id, db),
            "active_cameras": {
                "online": sum(1 for camera in cameras if getattr(camera, "is_active", False)),
                "total": len(cameras),
            },
            "footfall_today": await _report_footfall_count(session.id, db),
        },
    }


@router.get("/today-attendance-log", dependencies=[Depends(require_authenticated)])
async def today_attendance_log(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    session = await _today_session(auth, db)
    if session is None:
        return {"session": None, "rows": []}

    employees_result = await db.execute(
        select(Employee)
        .where(Employee.tenant_id == auth.tenant_id, Employee.status == "active")
        .order_by(Employee.name.asc())
    )
    employees = employees_result.scalars().all()

    attendance_result = await db.execute(
        select(AttendanceEvent).where(
            AttendanceEvent.session_id == session.id,
            AttendanceEvent.event_type == "check_in",
        )
    )
    attendance_events = attendance_result.scalars().all()

    office_day = datetime.now(overview_route.OFFICE_TZ).date()
    first_check_in_by_employee_id: dict[uuid.UUID, datetime] = {}
    for event in attendance_events:
        employee_id = getattr(event, "employee_id", None)
        event_time = overview_route._to_office_time(getattr(event, "time", None))
        if employee_id is None or event_time is None or event_time.date() != office_day:
            continue
        current = first_check_in_by_employee_id.get(employee_id)
        if current is None or event_time < current:
            first_check_in_by_employee_id[employee_id] = event_time

    rows: list[dict] = []
    for employee in employees:
        person_result = await db.execute(
            select(SessionPerson)
            .where(SessionPerson.session_id == session.id, SessionPerson.employee_id == employee.id)
            .order_by(SessionPerson.last_seen_at.desc())
            .limit(1)
        )
        person = _scalar_result_value(person_result)
        person_record_id = getattr(person, "id", getattr(person, "session_person_id", None))

        productivity_result = await db.execute(
            select(func.avg(ActivityEvent.confidence)).where(
                ActivityEvent.session_id == session.id,
                ActivityEvent.session_person_id == person_record_id,
            )
        )
        productivity_percent = int(round(float(_scalar_result_value(productivity_result) or 0)))

        violations_result = await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.session_id == session.id,
                Alert.session_person_id == person_record_id,
            )
        )
        violations = int(_scalar_result_value(violations_result) or 0)

        first_entry = first_check_in_by_employee_id.get(employee.id)
        if first_entry is None:
            status = "absent"
        elif overview_route.LATE_CUTOFF < first_entry.time() < overview_route.ABSENT_CUTOFF:
            status = "late"
        else:
            status = "on_time"

        total_work_minutes = 0
        if getattr(person, "first_seen_at", None) and getattr(person, "last_seen_at", None):
            total_work_minutes = int(
                round((person.last_seen_at - person.first_seen_at).total_seconds() / 60.0)
            )

        rows.append(
            {
                "employee_id": str(employee.id),
                "employee_name": employee.name,
                "department": getattr(employee, "department", None),
                "first_entry": _utc_text(first_entry.astimezone(timezone.utc) if first_entry else None),
                "last_seen": _utc_text(getattr(person, "last_seen_at", None)),
                "total_work_minutes": total_work_minutes,
                "status": status,
                "violations": violations,
                "productivity_percent": productivity_percent,
            }
        )

    return {
        "session": _session_stub(session),
        "rows": rows,
    }


async def _today_insights_payload(auth: AuthState, db: AsyncSession):
    session = await _today_session(auth, db)
    if session is None:
        return {
            "session": None,
            "cards": {
                "late_arrivals": [],
                "on_time": [],
                "phone_usage": [],
                "violations": [],
            },
        }

    attendance_log = await today_attendance_log(auth=auth, db=db)
    rows = attendance_log["rows"]

    people_result = await db.execute(
        select(SessionPerson).where(
            SessionPerson.session_id == session.id,
            SessionPerson.employee_id.is_not(None),
        )
    )
    persons = people_result.scalars().all()
    person_ids_by_employee: dict[str, list[uuid.UUID]] = {}
    for person in persons:
        employee_id = getattr(person, "employee_id", None)
        person_id = getattr(person, "id", None)
        if employee_id is None or person_id is None:
            continue
        person_ids_by_employee.setdefault(str(employee_id), []).append(person_id)

    phone_usage = []
    for row in rows:
        person_ids = person_ids_by_employee.get(row["employee_id"], [])
        if not person_ids:
            continue
        phone_result = await db.execute(
            select(func.sum(PhoneEvent.duration_seconds)).where(
                PhoneEvent.session_person_id.in_(person_ids)
            )
        )
        total_seconds = float(_scalar_result_value(phone_result) or 0)
        if total_seconds <= 0:
            continue
        phone_usage.append(
            {
                "employee_id": row["employee_id"],
                "employee_name": row["employee_name"],
                "department": row["department"],
                "phone_usage_minutes": round(total_seconds / 60.0, 1),
            }
        )

    phone_usage.sort(key=lambda item: (-item["phone_usage_minutes"], item["employee_name"]))

    return {
        "session": _session_stub(session),
        "cards": {
            "late_arrivals": [
                {
                    "employee_id": row["employee_id"],
                    "employee_name": row["employee_name"],
                    "department": row["department"],
                    "first_entry": row["first_entry"],
                }
                for row in rows
                if row["status"] == "late"
            ],
            "on_time": [
                {
                    "employee_id": row["employee_id"],
                    "employee_name": row["employee_name"],
                    "department": row["department"],
                    "first_entry": row["first_entry"],
                }
                for row in rows
                if row["status"] == "on_time"
            ],
            "phone_usage": phone_usage,
            "violations": [
                {
                    "employee_id": row["employee_id"],
                    "employee_name": row["employee_name"],
                    "department": row["department"],
                    "violations": row["violations"],
                }
                for row in sorted(rows, key=lambda item: (-item["violations"], item["employee_name"]))
                if row["violations"] > 0
            ],
        },
    }


@router.get("/today-insights", dependencies=[Depends(require_authenticated)])
async def today_insights(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    return await _today_insights_payload(auth, db)


@router.get("/day-summary", dependencies=[Depends(require_authenticated)])
async def day_summary(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    return await _day_summary_payload(datetime.fromisoformat(date).date(), auth, db)


@router.get("/monthly-timeline", dependencies=[Depends(require_authenticated)])
async def monthly_timeline(
    days: int = Query(30, ge=1, le=90),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    return await _monthly_timeline_payload(days, auth, db)


@router.get("/employee-analysis", dependencies=[Depends(require_authenticated)])
async def employee_analysis(
    days: int = Query(30, ge=1, le=90),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    return await _employee_analysis_payload(days, auth, db)


@router.get("/leaderboards", dependencies=[Depends(require_authenticated)])
async def leaderboards(
    days: int = Query(30, ge=1, le=90),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    return await _leaderboards_payload(days, auth, db)


@router.get("/sessions/{session_id}/summary", dependencies=[Depends(require_authenticated)])
async def session_summary(session_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    persons_count = await db.execute(
        select(func.count()).select_from(SessionPerson).where(SessionPerson.session_id == session_id)
    )
    alerts_count = await db.execute(
        select(func.count()).select_from(Alert).where(Alert.session_id == session_id)
    )
    attendance_count = await db.execute(
        select(func.count()).select_from(AttendanceEvent).where(AttendanceEvent.session_id == session_id)
    )
    return {
        "session_id": str(session_id),
        "total_persons": persons_count.scalar_one_or_none() or 0,
        "total_alerts": alerts_count.scalar_one_or_none() or 0,
        "total_check_ins": attendance_count.scalar_one_or_none() or 0,
    }
