"""Identity review actions for human-in-the-loop merge decisions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.minio_client import get_presigned_url
from api.auth import AuthState, require_admin
from api.identity_review_service import IdentityReviewService

router = APIRouter()


class MergeUnknownPersonsRequest(BaseModel):
    source_person_id: uuid.UUID
    target_person_ids: list[uuid.UUID] = Field(min_length=1)
    reason: str | None = None


class AssignEmployeeRequest(BaseModel):
    source_person_id: uuid.UUID
    employee_id: uuid.UUID
    reason: str | None = None


class UndoIdentityReviewRequest(BaseModel):
    reason: str | None = None


class IdentityReviewQueueResponse(BaseModel):
    scope: str
    items: list[dict]


class IdentityReviewHistoryResponse(BaseModel):
    items: list[dict]


def _public_asset_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if "://" in normalized or normalized.startswith("data:") or normalized.startswith("blob:"):
        return normalized
    try:
        return get_presigned_url(normalized.lstrip("/"))
    except Exception:
        return normalized


def _normalize_review_queue_item(item: dict) -> dict:
    return {
        **item,
        "representative_thumbnail_url": _public_asset_url(item.get("representative_thumbnail_url")),
    }


def _normalize_review_item_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    source = dict(payload.get("source") or {})
    candidates = [dict(candidate) for candidate in payload.get("candidates", [])]
    source["best_thumbnail_url"] = _public_asset_url(source.get("best_thumbnail_url"))
    for candidate in candidates:
        candidate["best_thumbnail_url"] = _public_asset_url(candidate.get("best_thumbnail_url"))
    return {
        **payload,
        "source": source,
        "candidates": candidates,
    }


def _cluster_payload(cluster) -> dict:
    return {
        "id": str(cluster.id),
        "employee_id": str(cluster.employee_id) if getattr(cluster, "employee_id", None) else None,
        "cluster_state": getattr(cluster, "cluster_state", None),
        "display_label": getattr(cluster, "display_label", None),
    }


def _review_payload(review) -> dict:
    return {
        "id": str(review.id),
        "type": getattr(review, "review_type", None),
        "decision": getattr(review, "decision", None),
    }


def _person_payload(person) -> dict:
    return {
        "id": str(person.id),
        "employee_id": str(person.employee_id) if getattr(person, "employee_id", None) else None,
        "recognition_state": getattr(person, "recognition_state", None),
        "identity_cluster_id": (
            str(person.identity_cluster_id) if getattr(person, "identity_cluster_id", None) else None
        ),
    }


@router.get("/queue", dependencies=[Depends(require_admin)], response_model=IdentityReviewQueueResponse)
async def get_identity_review_queue(
    scope: str = Query(default="active_session"),
    session_id: uuid.UUID | None = Query(default=None),
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    items = await service.list_review_queue(
        auth=auth,
        scope=scope,
        session_id=session_id,
    )
    return {
        "scope": scope,
        "items": [_normalize_review_queue_item(item) for item in items],
    }


@router.get("/items/{item_id}", dependencies=[Depends(require_admin)])
async def get_identity_review_item(
    item_id: str,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    return _normalize_review_item_payload(
        await service.get_review_item(
            auth=auth,
            item_id=item_id,
        )
    )

@router.get("/history", dependencies=[Depends(require_admin)], response_model=IdentityReviewHistoryResponse)
async def get_identity_review_history(
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    return {
        "items": await service.list_review_history(auth=auth),
    }


@router.post("/merge", dependencies=[Depends(require_admin)])
async def merge_unknown_persons(
    body: MergeUnknownPersonsRequest,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    result = await service.merge_unknown_persons(
        auth=auth,
        source_person_id=body.source_person_id,
        target_person_ids=body.target_person_ids,
        reason=body.reason,
    )
    await db.commit()
    return {
        "cluster": _cluster_payload(result.cluster),
        "review": _review_payload(result.review),
        "merged_person_ids": [str(person.id) for person in result.persons],
    }


@router.post("/assign-employee", dependencies=[Depends(require_admin)])
async def assign_employee(
    body: AssignEmployeeRequest,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    result = await service.assign_person_to_employee(
        auth=auth,
        source_person_id=body.source_person_id,
        employee_id=body.employee_id,
        reason=body.reason,
    )
    await db.commit()
    return {
        "cluster": _cluster_payload(result.cluster),
        "review": _review_payload(result.review),
        "merged_person_ids": [str(person.id) for person in result.persons],
    }


@router.post("/{review_id}/undo", dependencies=[Depends(require_admin)])
async def undo_identity_review(
    review_id: uuid.UUID,
    body: UndoIdentityReviewRequest,
    auth: AuthState = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    service = IdentityReviewService(db)
    result = await service.undo_identity_review(
        auth=auth,
        review_id=review_id,
        reason=body.reason,
    )
    await db.commit()
    return {
        "review": _review_payload(result.review),
        "person": _person_payload(result.person),
    }
