"""Runtime observability state for face training and system metrics."""

from __future__ import annotations

import base64
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from airco.minio_client import delete_object, delete_employee_face, upload_employee_face

logger = logging.getLogger(__name__)

MAX_REJECTED_PREVIEWS_PER_JOB = 10


@dataclass
class FaceTrainingJobRuntimeState:
    current_face_image: str | None = None
    last_accepted_image: str | None = None
    last_rejected_image: str | None = None
    rejection_reason: str | None = None
    queue_depth: int = 0
    worker_states: dict[str, str] = field(default_factory=dict)
    embedding_total_ms: float = 0.0
    embedding_samples: int = 0
    images_uploaded: int = 0
    embeddings_completed: int = 0
    rejected_preview_objects: deque[str] = field(default_factory=deque)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


FACE_TRAINING_RUNTIME_STATE: dict[uuid.UUID, FaceTrainingJobRuntimeState] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode_bytes(image_bytes: bytes | bytearray | memoryview | None) -> str | None:
    if image_bytes is None:
        return None
    return base64.b64encode(bytes(image_bytes)).decode("ascii")


def _state(job_id: uuid.UUID) -> FaceTrainingJobRuntimeState:
    state = FACE_TRAINING_RUNTIME_STATE.get(job_id)
    if state is None:
        state = FaceTrainingJobRuntimeState()
        FACE_TRAINING_RUNTIME_STATE[job_id] = state
    return state


def reset_job_runtime_state(job_id: uuid.UUID) -> None:
    FACE_TRAINING_RUNTIME_STATE[job_id] = FaceTrainingJobRuntimeState()


def clear_job_runtime_state(job_id: uuid.UUID) -> None:
    FACE_TRAINING_RUNTIME_STATE.pop(job_id, None)


def record_current_face_preview(job_id: uuid.UUID, image_bytes: bytes | bytearray | memoryview | None) -> None:
    state = _state(job_id)
    state.current_face_image = _encode_bytes(image_bytes)
    state.updated_at = _now()


def record_accepted_face_preview(job_id: uuid.UUID, image_bytes: bytes | bytearray | memoryview | None) -> None:
    state = _state(job_id)
    encoded = _encode_bytes(image_bytes)
    state.current_face_image = encoded
    state.last_accepted_image = encoded
    state.updated_at = _now()


def record_rejected_face_preview(job_id: uuid.UUID, image_bytes: bytes | bytearray | memoryview | None, rejection_reason: str) -> None:
    state = _state(job_id)
    encoded = _encode_bytes(image_bytes)
    state.current_face_image = encoded
    state.last_rejected_image = encoded
    state.rejection_reason = rejection_reason
    state.updated_at = _now()


def record_rejected_preview_object(
    job_id: uuid.UUID,
    employee_name: str,
    camera_name: str,
    image_bytes: bytes,
    *,
    rejection_reason: str,
    frame_index: int | None = None,
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = f"-frame-{frame_index}" if frame_index is not None else ""
    reason_suffix = f"-{rejection_reason}" if rejection_reason else ""
    employee_segment = "".join(c.lower() if c.isalnum() else "_" for c in employee_name).strip("_") or "unknown"
    camera_segment = "".join(c.lower() if c.isalnum() else "_" for c in camera_name).strip("_") or "unknown"
    object_name = f"face-training-debug/{employee_segment}/{camera_segment}/{job_id}/{timestamp}{suffix}{reason_suffix}.jpg"
    stored_object_name = upload_employee_face(object_name, image_bytes, "image/jpeg")
    delete_object(object_name)

    state = _state(job_id)
    state.rejected_preview_objects.append(stored_object_name)
    while len(state.rejected_preview_objects) > MAX_REJECTED_PREVIEWS_PER_JOB:
        old_object = state.rejected_preview_objects.popleft()
        try:
            delete_employee_face(old_object)
        except Exception:
            logger.warning("Failed to delete stale rejected preview object %s", old_object, exc_info=True)

    state.updated_at = _now()
    return stored_object_name


def set_queue_depth(job_id: uuid.UUID, queue_depth: int) -> None:
    state = _state(job_id)
    state.queue_depth = max(0, int(queue_depth))
    state.updated_at = _now()


def set_worker_state(job_id: uuid.UUID, worker_name: str, worker_state: str) -> None:
    state = _state(job_id)
    state.worker_states[f"{job_id}:{worker_name}"] = worker_state
    state.updated_at = _now()


def record_embedding_timing(job_id: uuid.UUID, elapsed_ms: float) -> None:
    state = _state(job_id)
    state.embedding_total_ms += float(elapsed_ms)
    state.embedding_samples += 1
    state.embeddings_completed += 1
    state.updated_at = _now()


def record_image_uploaded(job_id: uuid.UUID, count: int = 1) -> None:
    state = _state(job_id)
    state.images_uploaded += max(0, int(count))
    state.updated_at = _now()


def aggregate_face_training_metrics() -> dict[str, Any]:
    queue_depth = 0
    workers_active = 0
    total_embedding_ms = 0.0
    total_embedding_samples = 0
    images_uploaded = 0
    embeddings_completed = 0
    worker_registry: dict[str, str] = {}

    for job_id, state in FACE_TRAINING_RUNTIME_STATE.items():
        queue_depth += int(state.queue_depth or 0)
        images_uploaded += int(state.images_uploaded or 0)
        embeddings_completed += int(state.embeddings_completed or 0)
        total_embedding_ms += float(state.embedding_total_ms or 0.0)
        total_embedding_samples += int(state.embedding_samples or 0)
        for worker_name, worker_state in state.worker_states.items():
            worker_registry[worker_name] = worker_state
            if worker_state not in {"idle", "finished", "stopped"}:
                workers_active += 1

    average_embedding_ms = round(total_embedding_ms / total_embedding_samples, 2) if total_embedding_samples else 0.0
    return {
        "queue_depth": queue_depth,
        "workers_active": workers_active,
        "average_embedding_ms": average_embedding_ms,
        "images_uploaded": images_uploaded,
        "embeddings_completed": embeddings_completed,
        "worker_registry": worker_registry,
    }


def build_preview_payload(job_id: uuid.UUID) -> dict[str, Any]:
    state = _state(job_id)
    return {
        "job_id": str(job_id),
        "current_face_image": state.current_face_image,
        "last_accepted_image": state.last_accepted_image,
        "last_rejected_image": state.last_rejected_image,
        "rejection_reason": state.rejection_reason,
    }
