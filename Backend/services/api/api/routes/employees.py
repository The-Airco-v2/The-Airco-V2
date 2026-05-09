"""Employee CRUD + face enrollment/training."""

from __future__ import annotations

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import Employee, EmployeeFaceTemplate, EmployeeFaceTrainingJob
from airco.minio_client import delete_employee_face, delete_object
from api.auth import AuthState, require_authenticated, require_admin
from api.face_training_service import (
    FaceTrainingCancelResponse,
    FaceTrainingStartRequest,
    FaceTrainingStatusResponse,
    cancel_face_training_job,
    get_face_training_status,
    start_face_training_job,
)

router = APIRouter()


class EmployeeCreate(BaseModel):
    name: str
    department: str | None = None


class EmployeeResponse(BaseModel):
    id: uuid.UUID
    name: str
    department: str | None
    enrollment_status: str
    photo_url: str | None
    created_at: datetime


async def _enrollment_status(db: AsyncSession, employee_id: uuid.UUID) -> str:
    result = await db.execute(
        select(func.count())
        .select_from(EmployeeFaceTemplate)
        .where(
            EmployeeFaceTemplate.employee_id == employee_id,
            EmployeeFaceTemplate.is_active == True,
        )
    )
    template_count = result.scalar_one()
    return "trained" if (template_count or 0) > 0 else "untrained"


async def _employee_response(db: AsyncSession, employee: Employee) -> EmployeeResponse:
    return EmployeeResponse(
        id=employee.id,
        name=employee.name,
        department=employee.department,
        enrollment_status=await _enrollment_status(db, employee.id),
        photo_url=None,
        created_at=employee.created_at,
    )


def _normalize_angle_label(angle: str) -> str:
    return {
        "front": "frontal",
    }.get(angle, angle)


async def _cleanup_employee_enrollment_data(
    *,
    db: AsyncSession,
    tenant_id: str,
    employee_id: uuid.UUID,
) -> None:
    """Cancel active face training and delete all enrollment artifacts for an employee."""
    job_result = await db.execute(
        select(EmployeeFaceTrainingJob).where(
            EmployeeFaceTrainingJob.employee_id == employee_id,
            EmployeeFaceTrainingJob.tenant_id == tenant_id,
            EmployeeFaceTrainingJob.status.in_(["capturing", "processing"]),
        )
    )
    active_jobs = job_result.scalars().all()
    if active_jobs:
        try:
            await cancel_face_training_job(tenant_id=tenant_id, employee_id=employee_id)
        except Exception:
            pass

    export_result = await db.execute(
        select(EmployeeFaceTrainingJob.export_object_name).where(
            EmployeeFaceTrainingJob.employee_id == employee_id,
            EmployeeFaceTrainingJob.tenant_id == tenant_id,
            EmployeeFaceTrainingJob.export_object_name.is_not(None),
        )
    )
    for (export_object_name,) in export_result.all():
        if export_object_name:
            try:
                delete_object(export_object_name)
            except Exception:
                pass

    templates_result = await db.execute(
        select(EmployeeFaceTemplate).where(
            EmployeeFaceTemplate.employee_id == employee_id,
            EmployeeFaceTemplate.sample_image_object_name.is_not(None),
        )
    )
    templates = templates_result.scalars().all()
    for template in templates:
        if template.sample_image_object_name:
            try:
                delete_employee_face(template.sample_image_object_name)
            except Exception:
                pass

    await db.execute(
        delete(EmployeeFaceTrainingJob).where(
            EmployeeFaceTrainingJob.employee_id == employee_id,
            EmployeeFaceTrainingJob.tenant_id == tenant_id,
        )
    )
    await db.execute(
        delete(EmployeeFaceTemplate).where(
            EmployeeFaceTemplate.employee_id == employee_id,
        )
    )


async def _load_employee(
    *,
    db: AsyncSession,
    tenant_id: str,
    employee_id: uuid.UUID,
) -> Employee:
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == tenant_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


class EnrollmentQuality(BaseModel):
    employee_id: str
    template_count: int
    angles_covered: list[str]
    missing_angles: list[str]
    quality_score: float


@router.post("", response_model=EmployeeResponse, status_code=201)
async def create_employee(
    body: EmployeeCreate,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    emp = Employee(
        tenant_id=auth.tenant_id,
        name=body.name,
        department=body.department,
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return await _employee_response(db, emp)


@router.get("", response_model=list[EmployeeResponse])
async def list_employees(
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(Employee).where(Employee.tenant_id == auth.tenant_id))
    employees = result.scalars().all()
    return [await _employee_response(db, employee) for employee in employees]


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    return await _employee_response(db, emp)


@router.delete("/{employee_id}", status_code=204)
async def delete_employee(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    employee = await _load_employee(db=db, tenant_id=auth.tenant_id, employee_id=employee_id)
    await _cleanup_employee_enrollment_data(db=db, tenant_id=auth.tenant_id, employee_id=employee_id)
    await db.delete(employee)
    await db.commit()


@router.delete("/{employee_id}/enrollment-data", status_code=204)
async def delete_employee_enrollment(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(404, "Employee not found")

    await _cleanup_employee_enrollment_data(db=db, tenant_id=auth.tenant_id, employee_id=employee_id)
    await db.commit()


@router.get("/{employee_id}/face-training/status", response_model=FaceTrainingStatusResponse)
async def face_training_status(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
):
    return await get_face_training_status(tenant_id=auth.tenant_id, employee_id=employee_id)


@router.post("/{employee_id}/face-training/start", response_model=FaceTrainingStatusResponse)
async def face_training_start(
    employee_id: uuid.UUID,
    body: FaceTrainingStartRequest,
    auth: AuthState = Depends(require_admin),
):
    return await start_face_training_job(
        tenant_id=auth.tenant_id,
        employee_id=employee_id,
        camera_id=body.camera_id,
        camera_name=body.camera_name,
        employee_name=body.employee_name,
        replace_existing=body.replace_existing,
        target_frames=body.target_frames,
        duration_seconds=body.duration_seconds,
        debug_mode=body.debug_mode,
    )


@router.post("/{employee_id}/face-training/cancel", response_model=FaceTrainingCancelResponse)
async def face_training_cancel(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_admin),
):
    return await cancel_face_training_job(tenant_id=auth.tenant_id, employee_id=employee_id)


@router.get("/{employee_id}/enrollment-quality", response_model=EnrollmentQuality)
async def enrollment_quality(
    employee_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    """Check enrollment coverage for an employee."""
    result = await db.execute(
        select(EmployeeFaceTemplate)
        .join(Employee, Employee.id == EmployeeFaceTemplate.employee_id)
        .where(
            EmployeeFaceTemplate.employee_id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    templates = result.scalars().all()

    all_angles = ["frontal", "left", "right", "up", "down", "seated"]
    covered = list(set(t.angle_label for t in templates if t.angle_label))
    missing = [a for a in all_angles if a not in covered]

    quality_score = len(covered) / len(all_angles) if all_angles else 0.0

    return EnrollmentQuality(
        employee_id=str(employee_id),
        template_count=len(templates),
        angles_covered=covered,
        missing_angles=missing,
        quality_score=round(quality_score, 2),
    )
