"""Analytics endpoints: employee history and cross-session trends."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import (
    Session, SessionPerson, AttendanceEvent, Alert,
    ActivityEvent, PhoneEvent, Employee,
)
from api.auth import AuthState, require_authenticated
from api.routes import exceptions as exceptions_route

router = APIRouter()


def _exception_item_created_at(item: dict) -> datetime | None:
    created_at = item.get("created_at")
    if not created_at:
        return None
    try:
        parsed = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(exceptions_route.OFFICE_TZ)


async def _open_exception_summary_for_session(
    session_id: uuid.UUID,
    auth: AuthState,
    db: AsyncSession,
) -> tuple[int, str | None]:
    """Count active exceptions using the queue as the source of truth and infer its office-day bucket."""
    items = await exceptions_route.list_exceptions(session_id=session_id, auth=auth, db=db)
    active_items = [item for item in items if item.get("status") in {"active", "pending"}]
    created_at_values = [
        created_at
        for created_at in (_exception_item_created_at(item) for item in active_items)
        if created_at is not None
    ]
    exception_day = max(created_at_values).date().isoformat() if created_at_values else None
    return len(active_items), exception_day


@router.get("/employees/{employee_id}/history", dependencies=[Depends(require_authenticated)])
async def employee_history(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    """All sessions an employee appeared in, with per-session stats."""
    # Verify employee exists AND belongs to this tenant
    emp_result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    emp = emp_result.scalar_one_or_none()
    if not emp:
        from fastapi import HTTPException
        raise HTTPException(404, "Employee not found")

    # Get all SessionPersons for this employee
    sp_result = await db.execute(
        select(SessionPerson).where(SessionPerson.employee_id == employee_id)
    )
    session_persons = sp_result.scalars().all()

    sessions_out = []
    for sp in session_persons:
        # Get session info
        sess_result = await db.execute(select(Session).where(Session.id == sp.session_id))
        sess = sess_result.scalar_one_or_none()
        if not sess:
            continue

        # Attendance events for this sp in this session
        # NOTE: event_type values are "check_in" and "check_out" (not "enter"/"exit")
        att_result = await db.execute(
            select(AttendanceEvent).where(
                AttendanceEvent.session_person_id == sp.id
            )
        )
        att_events = att_result.scalars().all()

        # Alert count for this sp
        alert_count_result = await db.execute(
            select(func.count()).select_from(Alert).where(
                Alert.session_person_id == sp.id
            )
        )
        alert_count = alert_count_result.scalar_one_or_none() or 0

        # Phone usage seconds
        phone_result = await db.execute(
            select(func.sum(PhoneEvent.duration_seconds)).where(
                PhoneEvent.session_person_id == sp.id
            )
        )
        phone_seconds = float(phone_result.scalar_one_or_none() or 0)

        # Activity counts
        activity_result = await db.execute(
            select(ActivityEvent.activity, func.count()).where(
                ActivityEvent.session_person_id == sp.id
            ).group_by(ActivityEvent.activity)
        )
        working = idle = 0
        for act, count in activity_result:
            if act == "working":
                working = count
            elif act == "idle":
                idle = count
        total_activity = working + idle
        productivity_pct = int((working / total_activity * 100) if total_activity > 0 else 0)

        # Dwell duration from first/last seen
        duration_minutes = None
        if sp.first_seen_at and sp.last_seen_at:
            delta = sp.last_seen_at - sp.first_seen_at
            duration_minutes = round(delta.total_seconds() / 60, 1)

        sessions_out.append({
            "session_id": str(sp.session_id),
            "session_name": sess.name,
            "session_status": sess.status,
            "date": sess.created_at.date().isoformat(),
            "entered_at": sp.first_seen_at.isoformat() if sp.first_seen_at else None,
            "left_at": sp.last_seen_at.isoformat() if sp.last_seen_at else None,
            "duration_minutes": duration_minutes,
            "check_ins": sum(1 for e in att_events if e.event_type == "check_in"),
            "check_outs": sum(1 for e in att_events if e.event_type == "check_out"),
            "productivity_percent": productivity_pct,
            "phone_usage_minutes": round(phone_seconds / 60, 1),
            "alert_count": alert_count,
        })

    # Sort newest first
    sessions_out.sort(key=lambda s: s["date"], reverse=True)

    return {
        "employee_id": str(employee_id),
        "employee_name": emp.name,
        "department": emp.department,
        "sessions": sessions_out,
        "total_sessions": len(sessions_out),
    }


@router.get("/trends", dependencies=[Depends(require_authenticated)])
async def cross_session_trends(
    days: int = Query(7, ge=1, le=90),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    """Cross-session analytics: productivity trend, alerts by day, attendance by day."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Sessions in period for this tenant
    sess_result = await db.execute(
        select(Session).where(
            Session.tenant_id == auth.tenant_id,
            Session.created_at >= since,
        ).order_by(Session.created_at)
    )
    sessions = sess_result.scalars().all()

    # Build per-day aggregates
    # day_key -> { date, sessions, total_persons, total_alerts, total_check_ins, avg_productivity }
    day_map: dict[str, dict] = {}

    for sess in sessions:
        open_exception_count, exception_day = await _open_exception_summary_for_session(sess.id, auth, db)
        day_key = exception_day or sess.created_at.date().isoformat()
        if day_key not in day_map:
            day_map[day_key] = {
                "date": day_key,
                "sessions": 0,
                "total_persons": 0,
                "total_alerts": 0,
                "total_check_ins": 0,
                "total_phone_seconds": 0.0,
                "total_open_exceptions": 0,
                "productivity_sum": 0,
                "productivity_count": 0,
            }
        entry = day_map[day_key]
        entry["sessions"] += 1

        # Persons
        person_count = await db.execute(
            select(func.count()).select_from(SessionPerson).where(SessionPerson.session_id == sess.id)
        )
        entry["total_persons"] += person_count.scalar_one_or_none() or 0

        # Alerts
        alert_count = await db.execute(
            select(func.count()).select_from(Alert).where(Alert.session_id == sess.id)
        )
        alert_total = alert_count.scalar_one_or_none() or 0
        entry["total_alerts"] += alert_total

        # Check-ins (event_type values are "check_in" / "check_out")
        checkin_count = await db.execute(
            select(func.count()).select_from(AttendanceEvent).where(
                AttendanceEvent.session_id == sess.id,
                AttendanceEvent.event_type == "check_in",
            )
        )
        entry["total_check_ins"] += checkin_count.scalar_one_or_none() or 0

        # Productivity: avg across all session persons
        sp_ids_result = await db.execute(
            select(SessionPerson.id).where(SessionPerson.session_id == sess.id)
        )
        sp_ids = [row[0] for row in sp_ids_result]
        for sp_id in sp_ids:
            act_result = await db.execute(
                select(ActivityEvent.activity, func.count()).where(
                    ActivityEvent.session_person_id == sp_id
                ).group_by(ActivityEvent.activity)
            )
            w = i = 0
            for act, count in act_result:
                if act == "working":
                    w = count
                elif act == "idle":
                    i = count
            total = w + i
            if total > 0:
                entry["productivity_sum"] += int(w / total * 100)
                entry["productivity_count"] += 1

        phone_seconds = await db.execute(
            select(func.sum(PhoneEvent.duration_seconds)).where(
                PhoneEvent.session_id == sess.id
            )
        )
        session_phone_seconds = float(phone_seconds.scalar_one_or_none() or 0)
        entry["total_phone_seconds"] += session_phone_seconds
        entry["total_open_exceptions"] += open_exception_count

    # Build output trend array
    trends = []
    for day_key in sorted(day_map.keys()):
        entry = day_map[day_key]
        avg_prod = (
            round(entry["productivity_sum"] / entry["productivity_count"])
            if entry["productivity_count"] > 0 else 0
        )
        trends.append({
            "date": entry["date"],
            "sessions": entry["sessions"],
            "total_persons": entry["total_persons"],
            "total_alerts": entry["total_alerts"],
            "total_check_ins": entry["total_check_ins"],
            "avg_productivity": avg_prod,
            "total_phone_minutes": round(entry["total_phone_seconds"] / 60, 1),
            "total_open_exceptions": entry["total_open_exceptions"],
        })

    return {
        "period_days": days,
        "trends": trends,
    }
