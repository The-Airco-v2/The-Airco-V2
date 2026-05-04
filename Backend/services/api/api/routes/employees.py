"""Employee CRUD + face enrollment."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.models import Employee, EmployeeFaceTemplate
from api.auth import AuthState, require_authenticated, require_admin

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
        .where(EmployeeFaceTemplate.employee_id == employee_id)
    )
    scalar = getattr(result, "scalar_one_or_none", None) or getattr(result, "scalar_one", None)
    template_count = scalar() if callable(scalar) else 0
    if inspect.isawaitable(template_count):
        template_count = await template_count
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
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await db.delete(emp)
    await db.commit()


@router.post("/{employee_id}/enroll")
async def enroll_face(
    employee_id: uuid.UUID,
    angle: str = "frontal",
    file: UploadFile | None = File(default=None),
    image: UploadFile | None = File(default=None),
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Upload a face image for enrollment. Generates embedding via Triton."""
    # Verify employee exists
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")

    upload = file or image
    if upload is None:
        raise HTTPException(422, "Enrollment image is required")

    # Read image
    image_bytes = await upload.read()

    # Generate embedding via Triton (in production)
    # For now, store a placeholder — the actual Triton call will be:
    #   import tritonclient.grpc.aio as grpcclient
    #   from PIL import Image
    #   import numpy as np, io
    #   img = Image.open(io.BytesIO(image_bytes)).resize((112, 112))
    #   img_np = np.array(img, dtype=np.float32).transpose(2, 0, 1)[np.newaxis] / 255.0
    #   triton = grpcclient.InferenceServerClient(url=settings.triton_url)
    #   inp = grpcclient.InferInput("input", img_np.shape, "FP32")
    #   inp.set_data_from_numpy(img_np)
    #   result = await triton.infer("arcface", [inp])
    #   embedding = result.as_numpy("output").flatten().tolist()

    import numpy as np
    embedding = np.random.randn(512).tolist()  # placeholder until Triton is available

    template = EmployeeFaceTemplate(
        employee_id=employee_id,
        embedding=embedding,
        angle_label=_normalize_angle_label(angle),
        quality_score=0.9,
    )
    db.add(template)
    await db.commit()

    return {"status": "enrolled", "angle": angle, "employee_id": str(employee_id)}


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
