"""Attendance events."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from airco.db import get_session
from airco.models import AttendanceEvent
from api.auth import require_authenticated

router = APIRouter()


class AttendanceResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    employee_id: uuid.UUID | None
    session_person_id: uuid.UUID
    camera_id: uuid.UUID | None
    event_type: str
    time: datetime
    model_config = {"from_attributes": True}


@router.get("", response_model=list[AttendanceResponse], dependencies=[Depends(require_authenticated)])
async def list_attendance(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(AttendanceEvent).where(AttendanceEvent.session_id == session_id)
        .order_by(AttendanceEvent.time.desc())
    )
    return result.scalars().all()
