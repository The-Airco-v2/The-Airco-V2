"""System health and face-training observability endpoints."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.config import settings
from airco.db import async_session, get_session
from airco.minio_client import get_minio
from airco.models import Employee, EmployeeFaceTrainingJob
from api.auth import AuthState, require_authenticated
from api.face_training_observability import aggregate_face_training_metrics, build_preview_payload
from api.face_training_service import TRITON_CLIENT, TRITON_TIMEOUT_SECONDS

router = APIRouter()

TRITON_MODEL_NAMES = ("scrfd", "arcface", "osnet")


class HealthServiceStatus(BaseModel):
    status: str


class TritonHealthResponse(BaseModel):
    status: str
    models_ready: list[str]
    models_unready: list[str]


class SystemHealthResponse(BaseModel):
    triton: TritonHealthResponse
    minio: HealthServiceStatus
    postgresql: HealthServiceStatus
    embedding_workers: dict[str, Any]


class FaceTrainingMetricsResponse(BaseModel):
    queue_depth: int
    workers_active: int
    average_embedding_ms: float
    images_uploaded: int
    embeddings_completed: int
    worker_registry: dict[str, str]


class FaceTrainingPreviewResponse(BaseModel):
    job_id: str
    current_face_image: str | None
    last_accepted_image: str | None
    last_rejected_image: str | None
    rejection_reason: str | None


async def _check_triton() -> TritonHealthResponse:
    models_ready: list[str] = []
    models_unready: list[str] = []

    try:
        live = await asyncio.wait_for(TRITON_CLIENT.is_server_live(), timeout=TRITON_TIMEOUT_SECONDS)
        ready = await asyncio.wait_for(TRITON_CLIENT.is_server_ready(), timeout=TRITON_TIMEOUT_SECONDS)
        for model_name in TRITON_MODEL_NAMES:
            try:
                model_ready = await asyncio.wait_for(
                    TRITON_CLIENT.is_model_ready(model_name=model_name),
                    timeout=TRITON_TIMEOUT_SECONDS,
                )
            except Exception:
                model_ready = False
            if model_ready:
                models_ready.append(model_name)
            else:
                models_unready.append(model_name)
        status = "online" if live and ready else "degraded"
    except Exception:
        status = "offline"
        models_unready = list(TRITON_MODEL_NAMES)

    return TritonHealthResponse(status=status, models_ready=models_ready, models_unready=models_unready)


async def _check_minio() -> HealthServiceStatus:
    try:
        client = get_minio()
        await asyncio.to_thread(client.bucket_exists, settings.minio_bucket)
        return HealthServiceStatus(status="online")
    except Exception:
        return HealthServiceStatus(status="offline")


async def _check_postgresql() -> HealthServiceStatus:
    try:
        async with async_session() as db:
            await db.scalar(select(1))
        return HealthServiceStatus(status="online")
    except Exception:
        return HealthServiceStatus(status="offline")


@router.get("/system/health", response_model=SystemHealthResponse)
async def system_health() -> SystemHealthResponse:
    triton_task = _check_triton()
    minio_task = _check_minio()
    postgres_task = _check_postgresql()
    triton, minio_status, postgres_status = await asyncio.gather(triton_task, minio_task, postgres_task)
    metrics = aggregate_face_training_metrics()
    return SystemHealthResponse(
        triton=triton,
        minio=minio_status,
        postgresql=postgres_status,
        embedding_workers={
            "active": metrics["workers_active"],
            "queue_depth": metrics["queue_depth"],
            "worker_registry": metrics["worker_registry"],
        },
    )


@router.get("/face-training/metrics", response_model=FaceTrainingMetricsResponse)
async def face_training_metrics(auth: AuthState = Depends(require_authenticated)) -> FaceTrainingMetricsResponse:
    _ = auth
    metrics = aggregate_face_training_metrics()
    return FaceTrainingMetricsResponse(**metrics)


@router.get("/face-training/{job_id}/preview", response_model=FaceTrainingPreviewResponse)
async def face_training_preview(
    job_id: uuid.UUID,
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
) -> FaceTrainingPreviewResponse:
    result = await db.execute(
        select(EmployeeFaceTrainingJob.id)
        .join(Employee, Employee.id == EmployeeFaceTrainingJob.employee_id)
        .where(
            EmployeeFaceTrainingJob.id == job_id,
            Employee.tenant_id == auth.tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(404, "Training job not found")
    return FaceTrainingPreviewResponse(**build_preview_payload(job_id))
