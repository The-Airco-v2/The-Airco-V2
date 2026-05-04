"""CSV export endpoints for attendance, alerts, and employee intelligence."""

from __future__ import annotations

import csv
import io
import uuid
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import (
    AttendanceEvent, Alert, SessionPerson, Employee,
    ActivityEvent, PhoneEvent,
)
from api.auth import require_authenticated

router = APIRouter()


def _csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/exports/attendance", dependencies=[Depends(require_authenticated)])
async def export_attendance(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(AttendanceEvent)
        .where(AttendanceEvent.session_id == session_id)
        .order_by(AttendanceEvent.time)
    )
    events = result.scalars().all()

    rows = [
        [
            e.time.isoformat(),
            e.event_type,
            str(e.employee_id) if e.employee_id else "",
            str(e.camera_id) if e.camera_id else "",
        ]
        for e in events
    ]
    return _csv_response(
        rows,
        ["time", "event_type", "employee_id", "camera_id"],
        f"attendance_{session_id}.csv",
    )


@router.get("/exports/alerts", dependencies=[Depends(require_authenticated)])
async def export_alerts(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Alert)
        .where(Alert.session_id == session_id)
        .order_by(Alert.created_at)
    )
    alerts = result.scalars().all()

    rows = [
        [
            a.created_at.isoformat(),
            a.alert_type,
            a.severity,
            a.message or "",
            a.status,  # "active", "acknowledged", or "resolved" — no is_acknowledged bool on model
        ]
        for a in alerts
    ]
    return _csv_response(
        rows,
        ["created_at", "alert_type", "severity", "message", "status"],
        f"alerts_{session_id}.csv",
    )


@router.get("/exports/employee-intelligence", dependencies=[Depends(require_authenticated)])
async def export_employee_intelligence(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_session),
):
    sp_result = await db.execute(
        select(SessionPerson).where(SessionPerson.session_id == session_id)
    )
    persons = sp_result.scalars().all()

    rows = []
    for sp in persons:
        emp_name = sp.display_name
        if sp.employee_id:
            emp_r = await db.execute(select(Employee.name).where(Employee.id == sp.employee_id))
            name = emp_r.scalar_one_or_none()
            if name:
                emp_name = name

        # Phone usage
        phone_r = await db.execute(
            select(func.sum(PhoneEvent.duration_seconds)).where(
                PhoneEvent.session_person_id == sp.id
            )
        )
        phone_seconds = float(phone_r.scalar_one_or_none() or 0)

        # Productivity
        act_r = await db.execute(
            select(ActivityEvent.activity, func.count()).where(
                ActivityEvent.session_person_id == sp.id
            ).group_by(ActivityEvent.activity)
        )
        working = idle = 0
        for act, count in act_r:
            if act == "working":
                working = count
            elif act == "idle":
                idle = count
        total = working + idle
        productivity_pct = int((working / total * 100) if total > 0 else 0)

        rows.append([
            emp_name,
            "trained" if sp.employee_id else "untrained",
            productivity_pct,
            round(phone_seconds / 60, 1),
            "yes" if phone_seconds > 30 else "no",
            "yes" if sp.is_active else "no",
        ])

    return _csv_response(
        rows,
        [
            "employee_name", "training_status", "productivity_percent",
            "phone_usage_minutes", "phone_violation", "is_present",
        ],
        f"employee_intelligence_{session_id}.csv",
    )
