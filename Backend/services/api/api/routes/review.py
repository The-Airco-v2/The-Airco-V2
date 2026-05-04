"""Review queue for identity corrections."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import ReviewTask, SessionPerson
from airco.config import settings
from api.auth import require_authenticated, require_admin

router = APIRouter()


class ReviewTaskResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    session_person_id: uuid.UUID | None
    task_type: str
    status: str
    evidence: dict | None
    decision: dict | None
    created_at: datetime
    model_config = {"from_attributes": True}


class ReviewDecision(BaseModel):
    decision: str  # "identify", "dismiss", "correct"
    employee_id: uuid.UUID | None = None
    notes: str | None = None


@router.get("/tasks", response_model=list[ReviewTaskResponse], dependencies=[Depends(require_authenticated)])
async def list_review_tasks(
    status: str = Query("pending"),
    session_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_session),
):
    query = select(ReviewTask).where(ReviewTask.status == status)
    if session_id:
        query = query.where(ReviewTask.session_id == session_id)
    query = query.order_by(ReviewTask.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/tasks/{task_id}", response_model=ReviewTaskResponse, dependencies=[Depends(require_authenticated)])
async def get_review_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Review task not found")
    return task


@router.post("/tasks/{task_id}/decide", dependencies=[Depends(require_admin)])
async def decide_review(
    task_id: uuid.UUID,
    body: ReviewDecision,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Review task not found")

    task.status = "resolved"
    task.decision = {
        "decision": body.decision,
        "employee_id": str(body.employee_id) if body.employee_id else None,
        "notes": body.notes,
    }
    task.resolved_at = datetime.now(timezone.utc)

    # Apply correction if applicable
    if body.decision == "correct" and body.employee_id and task.session_person_id:
        await db.execute(
            update(SessionPerson).where(SessionPerson.id == task.session_person_id).values(
                employee_id=body.employee_id,
                recognition_state="corrected",
            )
        )
    elif body.decision == "identify" and body.employee_id and task.session_person_id:
        await db.execute(
            update(SessionPerson).where(SessionPerson.id == task.session_person_id).values(
                employee_id=body.employee_id,
                recognition_state="identified",
            )
        )
    elif body.decision == "dismiss" and task.session_person_id:
        await db.execute(
            update(SessionPerson).where(SessionPerson.id == task.session_person_id).values(
                recognition_state="unknown",
            )
        )

    await db.commit()
    return {"status": "resolved", "decision": body.decision}
