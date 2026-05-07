"""Employee face training workflow — guided capture, quality filtering, ArcFace enrollment."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pickle
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from PIL import Image
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import tritonclient.grpc.aio as grpcclient
from tritonclient.grpc import InferInput, InferRequestedOutput

from airco.config import settings
from airco.db import async_session
from airco.minio_client import EMPLOYEE_BUCKET_NAME, get_minio, upload_employee_asset, upload_employee_face
from airco.models import Camera, Employee, EmployeeFaceTemplate, EmployeeFaceTrainingJob
from api.face_training_observability import (
    record_accepted_face_preview,
    record_current_face_preview,
    record_embedding_timing,
    record_image_uploaded,
    record_rejected_face_preview,
    record_rejected_preview_object,
    reset_job_runtime_state,
    set_queue_depth,
    set_worker_state,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ACTIVE_FACE_TRAINING_TASKS: dict[uuid.UUID, asyncio.Task[None]] = {}
ANGLE_LABELS = ("frontal", "left", "right", "up", "down")

DEFAULT_TARGET_FRAMES   = 100
DEFAULT_DURATION_SECONDS = 120
FACE_MIN_SIZE_PX        = 32
BLUR_THRESHOLD          = 25.0
LIGHT_LOW_THRESHOLD     = 20.0
LIGHT_HIGH_THRESHOLD    = 245.0
DUPLICATE_SIM_THRESHOLD = 0.80
MAX_PER_ANGLE           = 25
FACE_CROP_MARGIN        = 0.06

SCRFD_INPUT_SIZE = 640
SCRFD_SCORE_THRESHOLD = 0.45
SCRFD_NMS_THRESHOLD = 0.4
SCRFD_EXPECTED_OUTPUTS = 9
SCRFD_MAX_FACE_AREA_RATIO = 0.6
SCRFD_MIN_ASPECT_RATIO = 0.3
SCRFD_MAX_ASPECT_RATIO = 3.0
EMBEDDING_WORKERS = 3
TRAINING_QUEUE_SIZE = 12
PROGRESS_COMMIT_FRAME_INTERVAL = 10
PROGRESS_COMMIT_SECONDS = 2.0
TRITON_URL = settings.triton_url
TRITON_TIMEOUT_SECONDS = 5.0
SCRFD_TRITON_MODEL_NAME = "scrfd"
TRITON_CLIENT = grpcclient.InferenceServerClient(url=TRITON_URL)
SCRFD_TRITON_IO_NAMES: tuple[str, list[str]] | None = None
ARCFACE_INPUT_NAME = "input"
ARCFACE_OUTPUT_NAME = "output"

ARCFACE_REF_POINTS = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32,
)

# ── Schemas ──────────────────────────────────────────────────────────────────

class FaceTrainingStartRequest(BaseModel):
    camera_id: uuid.UUID
    camera_name: str
    employee_name: str
    replace_existing: bool = False
    target_frames: int = DEFAULT_TARGET_FRAMES
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    debug_mode: bool = False


class FaceTrainingStatusResponse(BaseModel):
    job_id: uuid.UUID | None
    employee_id: uuid.UUID
    employee_name: str
    camera_id: uuid.UUID | None
    camera_name: str | None
    state: str
    progress: int
    captured_frames: int
    accepted_frames: int
    uploaded_frames: int
    embedded_frames: int
    rejected_frames: int
    target_frames: int
    remaining_frames: int
    duration_seconds: int
    replace_existing: bool
    debug_mode: bool
    angle_coverage: dict[str, int]
    export_object_name: str | None
    error_message: str | None
    detector_face_count: int
    detector_confidence: float | None
    detector_bbox: list[float] | None
    rejection_reason: str | None
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None


class FaceTrainingCancelResponse(BaseModel):
    employee_id: uuid.UUID
    job_id: uuid.UUID | None
    state: str
    message: str

# ── RTSP helpers ─────────────────────────────────────────────────────────────

def _stream_name(camera_name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in camera_name).strip("_")

def _rtsp_url(camera_name: str) -> str:
    return f"rtsp://go2rtc:8556/{_stream_name(camera_name)}"

async def _open_capture(url: str) -> cv2.VideoCapture | None:
    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay",
    )
    cap = await asyncio.to_thread(cv2.VideoCapture, url, cv2.CAP_FFMPEG)
    for prop, val in [
        (cv2.CAP_PROP_BUFFERSIZE, 1),
        (getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None), 5_000),
        (getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None), 5_000),
    ]:
        if prop is not None:
            await asyncio.to_thread(cap.set, prop, val)
    if not await asyncio.to_thread(cap.isOpened):
        await asyncio.to_thread(cap.release)
        return None
    return cap

async def _read_frame(cap: cv2.VideoCapture) -> tuple[bool, np.ndarray | None]:
    return await asyncio.to_thread(cap.read)

# ── SCRFD detector ───────────────────────────────────────────────────────────

async def _scrfd_triton_io_names() -> tuple[str, list[str]]:
    global SCRFD_TRITON_IO_NAMES
    if SCRFD_TRITON_IO_NAMES is not None:
        return SCRFD_TRITON_IO_NAMES

    metadata = await asyncio.wait_for(
        TRITON_CLIENT.get_model_metadata(model_name=SCRFD_TRITON_MODEL_NAME),
        timeout=TRITON_TIMEOUT_SECONDS,
    )
    if not metadata.inputs:
        raise RuntimeError("SCRFD Triton model has no declared inputs")
    if not metadata.outputs:
        raise RuntimeError("SCRFD Triton model has no declared outputs")

    input_name = metadata.inputs[0].name
    output_names = [output.name for output in metadata.outputs]
    SCRFD_TRITON_IO_NAMES = (input_name, output_names)
    return SCRFD_TRITON_IO_NAMES


async def _infer_scrfd_triton(image: np.ndarray) -> list[np.ndarray]:
    input_name, output_names = await _scrfd_triton_io_names()

    input_tensor = image.astype(np.float32)
    infer_input = InferInput(input_name, input_tensor.shape, "FP32")
    infer_input.set_data_from_numpy(input_tensor)

    outputs = [InferRequestedOutput(name) for name in output_names]
    response = await asyncio.wait_for(
        TRITON_CLIENT.infer(
            model_name=SCRFD_TRITON_MODEL_NAME,
            inputs=[infer_input],
            outputs=outputs,
        ),
        timeout=TRITON_TIMEOUT_SECONDS,
    )
    if len(output_names) != SCRFD_EXPECTED_OUTPUTS:
        raise RuntimeError(
            f"SCRFD output count mismatch: expected {SCRFD_EXPECTED_OUTPUTS}, got {len(output_names)}"
        )

    tensors = [response.as_numpy(name) for name in output_names]
    if any(tensor is None for tensor in tensors):
        raise RuntimeError("SCRFD Triton response missing one or more output tensors")
    return [tensor for tensor in tensors if tensor is not None]


def _decode_scrfd(outputs: list[np.ndarray], det_size: int = SCRFD_INPUT_SIZE) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    strides, fmc, num_anchors = [8, 16, 32], 3, 2
    scores_l, bboxes_l, kps_l = [], [], []
    for i, stride in enumerate(strides):
        cls = outputs[i].flatten()
        mask = cls > SCRFD_SCORE_THRESHOLD
        if not mask.any():
            continue
        fh = fw = det_size // stride
        ax, ay = np.mgrid[:fh, :fw]
        anchors = np.repeat(np.stack([ay.ravel(), ax.ravel()], axis=1), num_anchors, axis=0).astype(np.float32)
        sa, sb, sk = anchors[mask], outputs[fmc + i][mask], outputs[2 * fmc + i][mask]
        cx, cy = (sa[:, 0] + 0.5) * stride, (sa[:, 1] + 0.5) * stride
        bbox = np.stack([cx - sb[:, 0]*stride, cy - sb[:, 1]*stride,
                         cx + sb[:, 2]*stride, cy + sb[:, 3]*stride], axis=1)
        kps = sk.copy()
        for k in range(5):
            kps[:, 2*k] = cx + sk[:, 2*k] * stride
            kps[:, 2*k+1] = cy + sk[:, 2*k+1] * stride
        scores_l.append(cls[mask]); bboxes_l.append(bbox); kps_l.append(kps)
    if not scores_l:
        return np.array([]), np.empty((0, 4)), np.empty((0, 10))
    return np.concatenate(scores_l), np.concatenate(bboxes_l), np.concatenate(kps_l)


def _nms(boxes: np.ndarray, scores: np.ndarray, thresh: float = SCRFD_NMS_THRESHOLD) -> list[int]:
    order, keep = scores.argsort()[::-1], []
    while len(order):
        i = int(order[0]); keep.append(i)
        if len(order) == 1: break
        rest = order[1:]
        inter = (np.maximum(0, np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0])) *
                 np.maximum(0, np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1])))
        iou = inter / ((boxes[i,2]-boxes[i,0])*(boxes[i,3]-boxes[i,1]) +
                       (boxes[rest,2]-boxes[rest,0])*(boxes[rest,3]-boxes[rest,1]) - inter + 1e-6)
        order = rest[iou < thresh]
    return keep


def _is_face_like_bbox(bbox: list[float], frame_shape: tuple) -> bool:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bw, bh = x2 - x1, y2 - y1
    if bw < FACE_MIN_SIZE_PX or bh < FACE_MIN_SIZE_PX:
        return False
    fh, fw = frame_shape[:2]
    frame_area = float(max(fw * fh, 1))
    area_ratio = (bw * bh) / frame_area
    if area_ratio > SCRFD_MAX_FACE_AREA_RATIO:
        return False
    aspect = bw / max(bh, 1e-6)
    if aspect < SCRFD_MIN_ASPECT_RATIO or aspect > SCRFD_MAX_ASPECT_RATIO:
        return False
    return True


async def _detect_scrfd(frame: np.ndarray) -> list[dict[str, Any]]:
    h, w = frame.shape[:2]
    inp = cv2.resize(frame, (SCRFD_INPUT_SIZE, SCRFD_INPUT_SIZE)).astype(np.float32).transpose(2,0,1)[np.newaxis]
    try:
        outputs = await _infer_scrfd_triton(inp)
        scores, bboxes, kps = _decode_scrfd(outputs)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("SCRFD inference failed: %s", exc); return []
    if not len(scores):
        return []
    sx, sy = w / SCRFD_INPUT_SIZE, h / SCRFD_INPUT_SIZE
    out = []
    for idx in _nms(bboxes, scores):
        b = bboxes[idx]
        bbox = [max(0., b[0]*sx), max(0., b[1]*sy), min(float(w), b[2]*sx), min(float(h), b[3]*sy)]
        # Temporarily disabled face-like bbox filter for debugging
        # if not _is_face_like_bbox(bbox, frame.shape):
        #     continue
        if (bbox[2]-bbox[0]) < FACE_MIN_SIZE_PX or (bbox[3]-bbox[1]) < FACE_MIN_SIZE_PX:
            continue
        k = kps[idx].reshape(5, 2); k[:,0] *= sx; k[:,1] *= sy
        out.append({"bbox": bbox, "score": float(scores[idx]), "keypoints": k})
    return sorted(out, key=lambda d: d["score"], reverse=True)


def _detect_haar(frame: np.ndarray) -> list[dict[str, Any]]:
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        return []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    out = []
    for x, y, w, h in cascade.detectMultiScale(gray, 1.05, 3, minSize=(24, 24)):
        bbox = [float(x), float(y), float(x + w), float(y + h)]
        # Temporarily disabled face-like bbox filter for debugging
        # if _is_face_like_bbox(bbox, frame.shape):
        out.append({"bbox": [float(x), float(y), float(x+w), float(y+h)], "score": 0.5, "keypoints": None})
    return sorted(out, key=lambda d: (d["bbox"][2]-d["bbox"][0])*(d["bbox"][3]-d["bbox"][1]), reverse=True)


async def _detect(frame: np.ndarray) -> list[dict[str, Any]]:
    detections = await _detect_scrfd(frame)
    return detections or _detect_haar(frame)


def _best_detection(detections: list[dict[str, Any]], frame_shape: tuple) -> dict[str, Any] | None:
    if not detections:
        return None
    fh, fw = frame_shape[:2]
    cx, cy = fw / 2.0, fh / 2.0
    return max(detections, key=lambda d: (
        d.get("score", 0.0),
        -((d["bbox"][2]-d["bbox"][0]) * (d["bbox"][3]-d["bbox"][1])),
        -abs((d["bbox"][0]+d["bbox"][2])/2 - cx) - abs((d["bbox"][1]+d["bbox"][3])/2 - cy),
    ))

# ── Face utilities ───────────────────────────────────────────────────────────

def _align(img: np.ndarray, kps: np.ndarray, size: tuple[int,int] = (112,112)) -> np.ndarray | None:
    kps = np.asarray(kps, dtype=np.float32)
    if kps.shape != (5, 2) or float(np.std(kps, axis=0).sum()) < 1.0:
        return None
    tform, _ = cv2.estimateAffinePartial2D(kps, ARCFACE_REF_POINTS, method=cv2.LMEDS)
    return None if tform is None else cv2.warpAffine(img, tform, size, flags=cv2.INTER_LINEAR,
                                                      borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _crop(frame: np.ndarray, bbox: list[float], margin: float = 0.18) -> np.ndarray:
    x1, y1, x2, y2 = _crop_bounds(frame, bbox, margin=margin)
    return frame[y1:y2, x1:x2].copy()


def _crop_bounds(frame: np.ndarray, bbox: list[float], margin: float = 0.18) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    fh, fw = frame.shape[:2]
    left = max(0, int(x1 - w * margin))
    top = max(0, int(y1 - h * margin))
    right = min(fw, int(x2 + w * margin))
    bottom = min(fh, int(y2 + h * margin))
    if right <= left:
        right = min(fw, left + 1)
    if bottom <= top:
        bottom = min(fh, top + 1)
    return left, top, right, bottom


def _jpeg_bytes(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("Failed to encode preview image")
    return encoded.tobytes()


def _quality(face: np.ndarray) -> tuple[float, float, float]:
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()), float(gray.mean()), float(gray.std())


def _angle(bbox: list[float], frame_shape: tuple) -> str:
    fh, fw = frame_shape[:2]
    cx = ((bbox[0]+bbox[2])/2 - fw/2) / max(fw/2, 1)
    cy = ((bbox[1]+bbox[3])/2 - fh/2) / max(fh/2, 1)
    if abs(cx) > abs(cy):
        return "left" if cx <= -0.18 else ("right" if cx >= 0.18 else "frontal")
    return "up" if cy <= -0.18 else ("down" if cy >= 0.18 else "frontal")


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, np.float32), np.asarray(b, np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    return float(np.dot(va, vb) / (na * nb)) if na and nb else 0.0


def _normalize_bbox(bbox: list[float], shape: tuple) -> list[float]:
    fh, fw = shape[:2]
    return [max(0., min(1., v / (fw if i % 2 == 0 else fh))) for i, v in enumerate(bbox)]


def _object_segment(value: str) -> str:
    segment = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return segment or "unknown"


def _sample_object_name(employee_name: str, camera_name: str, job_id: uuid.UUID, sample_index: int) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    employee_segment = _object_segment(employee_name)
    camera_segment = _object_segment(camera_name)
    return f"{employee_segment}/{camera_segment}/{job_id}/{timestamp}-{sample_index:06d}.jpg"


def _download_employee_face_bytes(object_name: str) -> bytes:
    client = get_minio()
    response = client.get_object(EMPLOYEE_BUCKET_NAME, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _image_ahash(image: np.ndarray) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    mean = float(small.mean())
    value = 0
    for bit in (small > mean).flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _is_duplicate_hash(candidate_hash: int, seen_hashes: list[int], *, threshold: int = 6) -> bool:
    return any((candidate_hash ^ existing_hash).bit_count() <= threshold for existing_hash in seen_hashes)


def _is_occluded_detection(detection: dict[str, Any], frame_shape: tuple) -> bool:
    bbox = detection.get("bbox")
    if not bbox:
        return True
    fh, fw = frame_shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    if x1 <= 1.0 or y1 <= 1.0 or x2 >= float(fw) - 1.0 or y2 >= float(fh) - 1.0:
        return True
    keypoints = detection.get("keypoints")
    if keypoints is None:
        return False
    pts = np.asarray(keypoints, dtype=np.float32).reshape(-1, 2)
    margin = max(4.0, min(x2 - x1, y2 - y1) * 0.08)
    return bool(
        np.any(pts[:, 0] <= x1 + margin)
        or np.any(pts[:, 0] >= x2 - margin)
        or np.any(pts[:, 1] <= y1 + margin)
        or np.any(pts[:, 1] >= y2 - margin)
    )


def _phase_progress(phase: str, approved: int, embedded: int, target: int) -> int:
    target = max(target, 1)
    if phase == "embedding_processing":
        return int(min(100, 50 + round(embedded / target * 50)))
    if phase in {"capturing", "uploading"}:
        return int(min(50, round(approved / target * 50)))
    if phase == "completed":
        return 100
    if phase == "cancelled":
        return 0
    return int(min(100, round(approved / target * 100)))


def _progress(accepted: int, target: int) -> int:
    return int(min(100, round(accepted / max(target, 1) * 100)))


def _json_safe(v: Any) -> Any:
    if isinstance(v, np.ndarray): return v.tolist()
    if isinstance(v, np.generic): return v.item()
    if isinstance(v, dict): return {str(k): _json_safe(i) for k, i in v.items()}
    if isinstance(v, (list, tuple)): return [_json_safe(i) for i in v]
    return v


_json_safe_value = _json_safe


def _is_stale_scrfd_error(msg: str | None) -> bool:
    if not msg: return False
    n = msg.lower()
    return any(
        x in n
        for x in (
            "invalid_protobuf",
            "protobuf parsing failed",
            "scrfd model is missing",
            "scrfd detector load failed",
            "missinggreenlet",
            "greenlet_spawn",
            "pendingrollbackerror",
            "await_only",
            "invalid transaction",
        )
    )

def _log_training_rejection(job_id: uuid.UUID, *, frame_index: int, reason: str, detail: str | None = None) -> None:
    extra = f" detail={detail}" if detail else ""
    logger.info("Training rejection job=%s frame=%d reason=%s%s", job_id, frame_index, reason, extra)


def _log_training_debug(job_id: uuid.UUID, *, frame_index: int, face_count: int, confidence: float | None, bbox: list[float] | None) -> None:
    logger.debug("Training debug job=%s frame=%d faces=%d conf=%s bbox=%s", job_id, frame_index, face_count, confidence, bbox)


async def _assert_triton_models_ready() -> None:
    for model_name in (SCRFD_TRITON_MODEL_NAME, "arcface"):
        ready = await asyncio.wait_for(
            TRITON_CLIENT.is_model_ready(model_name=model_name),
            timeout=TRITON_TIMEOUT_SECONDS,
        )
        if not ready:
            raise RuntimeError(f"Triton model is not READY: {model_name}")


# ── ArcFace embedding ─────────────────────────────────────────────────────────

async def _embed(face: np.ndarray) -> list[float]:
    arr = np.asarray(Image.fromarray(cv2.cvtColor(face, cv2.COLOR_BGR2RGB)).resize((112, 112)), dtype=np.float32)
    arr = arr.transpose(2, 0, 1)[np.newaxis] / 255.0
    inp = InferInput(ARCFACE_INPUT_NAME, arr.shape, "FP32")
    inp.set_data_from_numpy(arr)
    response = await asyncio.wait_for(
        TRITON_CLIENT.infer(
            "arcface",
            [inp],
            outputs=[InferRequestedOutput(ARCFACE_OUTPUT_NAME)],
        ),
        timeout=TRITON_TIMEOUT_SECONDS,
    )
    output = response.as_numpy(ARCFACE_OUTPUT_NAME)
    if output is None:
        raise RuntimeError("ArcFace Triton response missing output tensor")
    return output.flatten().tolist()

# ── DB helpers ────────────────────────────────────────────────────────────────

async def _latest_job(db: AsyncSession, employee_id: uuid.UUID) -> EmployeeFaceTrainingJob | None:
    r = await db.execute(
        select(EmployeeFaceTrainingJob).where(EmployeeFaceTrainingJob.employee_id == employee_id)
        .order_by(EmployeeFaceTrainingJob.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def _active_job(db: AsyncSession, employee_id: uuid.UUID) -> EmployeeFaceTrainingJob | None:
    r = await db.execute(
        select(EmployeeFaceTrainingJob).where(
            EmployeeFaceTrainingJob.employee_id == employee_id,
            EmployeeFaceTrainingJob.status.in_(("capturing", "uploading", "embedding_processing", "processing")),
        ).order_by(EmployeeFaceTrainingJob.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def _set_state(db: AsyncSession, job_id: uuid.UUID, **fields: Any) -> None:
    values = dict(fields)
    values["updated_at"] = fields.get("updated_at", datetime.now(timezone.utc))
    await db.execute(
        update(EmployeeFaceTrainingJob)
        .where(EmployeeFaceTrainingJob.id == job_id)
        .values(**values)
    )
    await db.commit()


async def _update_progress(
    db: AsyncSession, job: EmployeeFaceTrainingJob, *,
    captured: int, accepted: int, rejected: int, progress: int,
    angle_coverage: dict[str, int], status: str | None = None,
    face_count: int | None = None, confidence: float | None = None,
    bbox: list[float] | None = None, rejection_reason: str | None = None,
) -> None:
    job.captured_frames = captured
    job.accepted_frames = accepted
    job.rejected_frames = rejected
    job.progress = progress
    job.angle_coverage = _json_safe(angle_coverage)
    if status is not None:         job.status = status
    if face_count is not None:     job.detector_face_count = face_count
    if confidence is not None:     job.detector_confidence = float(confidence)
    if bbox is not None:           job.detector_bbox = _json_safe(bbox)
    if rejection_reason is not None: job.rejection_reason = rejection_reason
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def _job_cancel_requested(db: AsyncSession, job_id: uuid.UUID) -> bool:
    r = await db.execute(
        select(EmployeeFaceTrainingJob.cancel_requested).where(EmployeeFaceTrainingJob.id == job_id)
    )
    return bool(r.scalar_one_or_none())


async def _latest_template_version(db: AsyncSession, employee_id: uuid.UUID) -> int:
    r = await db.execute(
        select(EmployeeFaceTemplate.version).where(EmployeeFaceTemplate.employee_id == employee_id)
        .order_by(EmployeeFaceTemplate.version.desc()).limit(1)
    )
    return int(r.scalar_one_or_none() or 0)


def _idle_response(employee: Employee) -> FaceTrainingStatusResponse:
    return FaceTrainingStatusResponse(
        job_id=None, employee_id=employee.id, employee_name=employee.name,
        camera_id=None, camera_name=None, state="idle", progress=0,
        captured_frames=0, accepted_frames=0, rejected_frames=0,
        uploaded_frames=0, embedded_frames=0,
        target_frames=DEFAULT_TARGET_FRAMES, duration_seconds=DEFAULT_DURATION_SECONDS,
        remaining_frames=DEFAULT_TARGET_FRAMES,
        replace_existing=False, debug_mode=False,
        angle_coverage={l: 0 for l in ANGLE_LABELS},
        export_object_name=None, error_message=None,
        detector_face_count=0, detector_confidence=None, detector_bbox=None,
        rejection_reason=None, started_at=None, updated_at=None, finished_at=None,
    )


async def _job_payload(db: AsyncSession, *, employee: Employee, job: EmployeeFaceTrainingJob | None) -> FaceTrainingStatusResponse:
    if job is None:
        return _idle_response(employee)
    if job.status == "failed" and _is_stale_scrfd_error(job.error_message):
        return _idle_response(employee)

    camera_name = None
    if job.camera_id:
        r = await db.execute(select(Camera).where(Camera.id == job.camera_id))
        cam = r.scalar_one_or_none()
        camera_name = cam.name if cam else None

    coverage = {l: int((job.angle_coverage or {}).get(l, 0) or 0) for l in ANGLE_LABELS}
    sample_prefix = f"employee-faces/{_object_segment(employee.name)}/{_object_segment(camera_name or 'unknown')}/{job.id}/%"
    embedded_r = await db.execute(
        select(func.count()).select_from(EmployeeFaceTemplate).where(
            EmployeeFaceTemplate.employee_id == employee.id,
            EmployeeFaceTemplate.sample_image_object_name.like(sample_prefix),
        )
    )
    embedded_frames = int(embedded_r.scalar_one() or 0)
    uploaded_frames = int(job.accepted_frames or 0)
    remaining_frames = max(0, int(job.target_frames or DEFAULT_TARGET_FRAMES) - (
        embedded_frames if job.status in {"embedding_processing", "completed"} else uploaded_frames
    ))
    return FaceTrainingStatusResponse(
        job_id=job.id, employee_id=employee.id, employee_name=employee.name,
        camera_id=job.camera_id, camera_name=camera_name, state=job.status,
        progress=int(job.progress or 0), captured_frames=int(job.captured_frames or 0),
        accepted_frames=int(job.accepted_frames or 0), rejected_frames=int(job.rejected_frames or 0),
        uploaded_frames=uploaded_frames, embedded_frames=embedded_frames,
        target_frames=int(job.target_frames or DEFAULT_TARGET_FRAMES),
        remaining_frames=remaining_frames,
        duration_seconds=int(job.duration_seconds or DEFAULT_DURATION_SECONDS),
        replace_existing=bool(job.replace_existing), debug_mode=bool(getattr(job, "debug_mode", False)),
        angle_coverage=coverage, export_object_name=job.export_object_name,
        error_message=job.error_message,
        detector_face_count=int(getattr(job, "detector_face_count", 0) or 0),
        detector_confidence=(float(job.detector_confidence) if getattr(job, "detector_confidence", None) is not None else None),
        detector_bbox=job.detector_bbox, rejection_reason=job.rejection_reason,
        started_at=job.started_at, updated_at=job.updated_at, finished_at=job.finished_at,
    )

# ── Public API ────────────────────────────────────────────────────────────────

async def get_face_training_status(*, tenant_id: str, employee_id: uuid.UUID) -> FaceTrainingStatusResponse:
    async with async_session() as db:
        r = await db.execute(select(Employee).where(Employee.id == employee_id, Employee.tenant_id == tenant_id))
        emp = r.scalar_one_or_none()
        if emp is None:
            raise HTTPException(404, "Employee not found")
        return await _job_payload(db, employee=emp, job=await _latest_job(db, employee_id))


async def start_face_training_job(
    *, tenant_id: str, employee_id: uuid.UUID, camera_id: uuid.UUID,
    camera_name: str, employee_name: str,
    replace_existing: bool, target_frames: int = DEFAULT_TARGET_FRAMES,
    duration_seconds: int = DEFAULT_DURATION_SECONDS, debug_mode: bool = False,
) -> FaceTrainingStatusResponse:
    async with async_session() as db:
        emp_r = await db.execute(select(Employee).where(Employee.id == employee_id, Employee.tenant_id == tenant_id))
        emp = emp_r.scalar_one_or_none()
        if emp is None: raise HTTPException(404, "Employee not found")
        if emp.name != employee_name:
            raise HTTPException(409, "Selected employee no longer matches the current record")

        cam_r = await db.execute(select(Camera).where(Camera.id == camera_id, Camera.tenant_id == tenant_id))
        cam = cam_r.scalar_one_or_none()
        if cam is None: raise HTTPException(404, "Camera not found")
        if cam.name != camera_name:
            raise HTTPException(409, "Selected camera no longer matches the current record")
        if not cam.is_active: raise HTTPException(409, "Selected camera is offline")

        if await _active_job(db, employee_id):
            raise HTTPException(409, "Face training already in progress for this employee")

        job = EmployeeFaceTrainingJob(
            tenant_id=tenant_id, employee_id=employee_id, camera_id=camera_id,
            status="created", progress=0, captured_frames=0, accepted_frames=0, rejected_frames=0,
            target_frames=max(1, target_frames), duration_seconds=max(30, duration_seconds),
            replace_existing=replace_existing, debug_mode=debug_mode,
            angle_coverage={l: 0 for l in ANGLE_LABELS},
            export_object_name=None, error_message=None,
            detector_face_count=0, detector_confidence=None, detector_bbox=None,
            rejection_reason=None, started_at=datetime.now(timezone.utc),
        )
        db.add(job); await db.commit(); await db.refresh(job)

    task = asyncio.create_task(_run_face_training_job(job.id), name=f"face-training-{job.id}")
    ACTIVE_FACE_TRAINING_TASKS[job.id] = task
    task.add_done_callback(lambda _: ACTIVE_FACE_TRAINING_TASKS.pop(job.id, None))
    return await get_face_training_status(tenant_id=tenant_id, employee_id=employee_id)


async def cancel_face_training_job(*, tenant_id: str, employee_id: uuid.UUID) -> FaceTrainingCancelResponse:
    async with async_session() as db:
        r = await db.execute(select(Employee).where(Employee.id == employee_id, Employee.tenant_id == tenant_id))
        if r.scalar_one_or_none() is None:
            raise HTTPException(404, "Employee not found")
        job = await _active_job(db, employee_id)
        if job is None:
            return FaceTrainingCancelResponse(employee_id=employee_id, job_id=None, state="idle", message="No active training job")
        job.status = "cancelled"
        job.cancel_requested = True
        job.finished_at = job.updated_at = datetime.now(timezone.utc)
        await db.commit()
    return FaceTrainingCancelResponse(employee_id=employee_id, job_id=job.id, state="cancelled", message="Cancellation requested")

# ── Core training loop ────────────────────────────────────────────────────────

async def _run_face_training_job(job_id: uuid.UUID) -> None:
    async with async_session() as db:
        job_r = await db.execute(
            select(
                EmployeeFaceTrainingJob.tenant_id,
                EmployeeFaceTrainingJob.employee_id,
                EmployeeFaceTrainingJob.camera_id,
                EmployeeFaceTrainingJob.target_frames,
                EmployeeFaceTrainingJob.duration_seconds,
                EmployeeFaceTrainingJob.replace_existing,
                EmployeeFaceTrainingJob.debug_mode,
            ).where(EmployeeFaceTrainingJob.id == job_id)
        )
        job_row = job_r.one_or_none()
        if job_row is None:
            return
        tenant_id, employee_id, camera_id, target_frames_value, duration_seconds_value, replace_existing, debug_mode = job_row
        reset_job_runtime_state(job_id)
        try:
            emp_r = await db.execute(select(Employee).where(Employee.id == employee_id, Employee.tenant_id == tenant_id))
            cam_r = await db.execute(select(Camera).where(Camera.id == camera_id, Camera.tenant_id == tenant_id))
            emp, cam = emp_r.scalar_one_or_none(), cam_r.scalar_one_or_none()
            if emp is None or cam is None:
                raise HTTPException(404, "Employee or camera not found")
            employee = emp
            camera = cam
            if await _job_cancel_requested(db, job_id):
                return

            await _assert_triton_models_ready()

            target_frames = int(target_frames_value or DEFAULT_TARGET_FRAMES)
            duration_seconds = int(duration_seconds_value or DEFAULT_DURATION_SECONDS)
            replace_existing = bool(replace_existing)
            debug_mode = bool(debug_mode)

            await _set_state(db, job_id, status="capturing", updated_at=datetime.now(timezone.utc))

            logger.info(
                "[face-training] Starting capture for employee %s on camera %s (ID: %s) using RTSP URL: %s",
                employee.name, camera.name, str(camera.id), cam.rtsp_url
            )
            capture = await _open_capture(cam.rtsp_url)
            if capture is None:
                raise HTTPException(409, "Unable to open camera stream for training")

            version_floor = await _latest_template_version(db, employee.id)

            try:
                accepted_samples: list[dict[str, Any]] = []
                seen_hashes: list[int] = []
                angle_counts: dict[str, int] = {label: 0 for label in ANGLE_LABELS}
                state: dict[str, Any] = {
                    "captured": 0,
                    "uploaded": 0,
                    "embedded": 0,
                    "rejected": 0,
                    "phase": "capturing",
                    "detector_face_count": 0,
                    "detector_confidence": None,
                    "detector_bbox": None,
                    "rejection_reason": None,
                    "export_object_name": None,
                    "error_message": None,
                    "finished_at": None,
                }
                state_lock = asyncio.Lock()
                persist_lock = asyncio.Lock()
                progress_event = asyncio.Event()
                reporter_done_event = asyncio.Event()
                next_template_version = version_floor

                for index in range(EMBEDDING_WORKERS):
                    set_worker_state(job_id, f"embedding_worker_{index + 1}", "idle")
                set_queue_depth(job_id, 0)

                async def _snapshot() -> dict[str, Any]:
                    async with state_lock:
                        approved = len(accepted_samples)
                        embedded = int(state["embedded"])
                        phase = str(state["phase"])
                        return {
                            "status": phase if phase != "capturing" else "capturing",
                            "captured": int(state["captured"]),
                            "rejected": int(state["rejected"]),
                            "accepted": approved,
                            "uploaded": int(state["uploaded"]),
                            "embedded": embedded,
                            "remaining": max(0, target_frames - (embedded if phase in {"embedding_processing", "completed"} else approved)),
                            "progress": _phase_progress(phase, approved, embedded, target_frames),
                            "angle_coverage": dict(angle_counts),
                            "detector_face_count": state["detector_face_count"],
                            "detector_confidence": state["detector_confidence"],
                            "detector_bbox": state["detector_bbox"],
                            "rejection_reason": state["rejection_reason"],
                            "export_object_name": state["export_object_name"],
                            "error_message": state["error_message"],
                            "finished_at": state["finished_at"],
                        }

                async def _persist_snapshot(snapshot: dict[str, Any]) -> None:
                    async with async_session() as progress_db:
                        await progress_db.execute(
                            update(EmployeeFaceTrainingJob)
                            .where(EmployeeFaceTrainingJob.id == job_id)
                            .values(
                                status=snapshot["status"],
                                captured_frames=int(snapshot["captured"]),
                                accepted_frames=int(snapshot["accepted"]),
                                rejected_frames=int(snapshot["rejected"]),
                                progress=int(snapshot["progress"]),
                                angle_coverage=_json_safe(snapshot["angle_coverage"]),
                                detector_face_count=int(snapshot["detector_face_count"] or 0),
                                detector_confidence=(
                                    float(snapshot["detector_confidence"])
                                    if snapshot["detector_confidence"] is not None
                                    else None
                                ),
                                detector_bbox=_json_safe(snapshot["detector_bbox"]),
                                rejection_reason=snapshot["rejection_reason"],
                                export_object_name=snapshot["export_object_name"],
                                error_message=snapshot["error_message"],
                                updated_at=datetime.now(timezone.utc),
                                finished_at=snapshot["finished_at"],
                            )
                        )
                        await progress_db.commit()

                async def _progress_reporter() -> None:
                    last_committed_progress = -1
                    last_committed_at = time.monotonic()
                    while True:
                        try:
                            await asyncio.wait_for(progress_event.wait(), timeout=0.5)
                        except asyncio.TimeoutError:
                            pass
                        progress_event.clear()
                        snapshot = await _snapshot()
                        now = time.monotonic()
                        should_commit = (
                            snapshot["progress"] != last_committed_progress
                            or now - last_committed_at >= PROGRESS_COMMIT_SECONDS
                            or reporter_done_event.is_set()
                        )
                        if not should_commit:
                            continue
                        await _persist_snapshot(snapshot)
                        last_committed_progress = snapshot["progress"]
                        last_committed_at = now
                        if reporter_done_event.is_set():
                            return

                async def _capture_producer() -> None:
                    nonlocal angle_counts
                    last_failure_count = 0
                    start = time.monotonic()

                    async def _mark_rejection(
                        *,
                        image: np.ndarray | None,
                        frame_index: int,
                        reason: str,
                        detail: str | None = None,
                        detector_face_count: int = 0,
                        detector_confidence: float | None = None,
                        detector_bbox: list[float] | None = None,
                    ) -> None:
                        if image is not None:
                            preview_bytes = _jpeg_bytes(image)
                            record_current_face_preview(job_id, preview_bytes)
                            record_rejected_face_preview(job_id, preview_bytes, reason)
                            record_rejected_preview_object(
                                job_id,
                                employee.name,
                                camera.name,
                                preview_bytes,
                                rejection_reason=reason,
                                frame_index=frame_index,
                            )
                        _log_training_rejection(job_id, frame_index=frame_index, reason=reason, detail=detail)
                        async with state_lock:
                            state["rejected"] += 1
                            state["detector_face_count"] = detector_face_count
                            state["detector_confidence"] = detector_confidence
                            state["detector_bbox"] = detector_bbox
                            state["rejection_reason"] = reason
                        progress_event.set()

                    try:
                        while True:
                            if await _job_cancel_requested(db, job_id):
                                async with state_lock:
                                    state["phase"] = "cancelled"
                                    state["finished_at"] = datetime.now(timezone.utc)
                                progress_event.set()
                                return

                            now = time.monotonic()
                            async with state_lock:
                                approved_count = len(accepted_samples)
                            if approved_count >= target_frames:
                                return
                            if now - start >= duration_seconds:
                                return

                            ok, frame = await _read_frame(capture)
                            if not ok or frame is None:
                                last_failure_count += 1
                                _log_training_rejection(
                                    job_id,
                                    frame_index=int(state["captured"]),
                                    reason="camera_frame_unavailable",
                                    detail=f"failure_count={last_failure_count}",
                                )
                                async with state_lock:
                                    state["captured"] += 1
                                    state["rejected"] += 1
                                    state["detector_face_count"] = 0
                                    state["detector_confidence"] = None
                                    state["detector_bbox"] = None
                                    state["rejection_reason"] = "camera_frame_unavailable"
                                progress_event.set()
                                if last_failure_count >= 10:
                                    raise HTTPException(409, "Camera stream is not producing frames")
                                continue

                            last_failure_count = 0
                            async with state_lock:
                                state["captured"] += 1
                                frame_index = int(state["captured"])

                            detections = await _detect(frame)
                            face_count = len(detections)
                            top_detection = _best_detection(detections, frame.shape)
                            detector_confidence = float(top_detection["score"]) if top_detection else None
                            detector_bbox = _normalize_bbox(top_detection["bbox"], frame.shape) if top_detection else None

                            face_input: np.ndarray | None = None
                            if top_detection is not None:
                                face_box = [float(value) for value in top_detection["bbox"]]
                                crop_x1, crop_y1, crop_x2, crop_y2 = _crop_bounds(frame, face_box, margin=FACE_CROP_MARGIN)
                                face_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2].copy()
                                keypoints = top_detection.get("keypoints")
                                aligned_face = None
                                if keypoints is not None:
                                    local_keypoints = np.asarray(keypoints, dtype=np.float32).copy()
                                    local_keypoints[:, 0] -= float(crop_x1)
                                    local_keypoints[:, 1] -= float(crop_y1)
                                    aligned_face = _align(face_crop, local_keypoints)
                                face_input = aligned_face if aligned_face is not None else face_crop
                                record_current_face_preview(job_id, _jpeg_bytes(face_input))

                            if debug_mode:
                                _log_training_debug(
                                    job_id,
                                    frame_index=frame_index,
                                    face_count=face_count,
                                    confidence=detector_confidence,
                                    bbox=detector_bbox,
                                )

                            if face_count == 0 or top_detection is None:
                                await _mark_rejection(
                                    image=None,
                                    frame_index=frame_index,
                                    reason="no_face_detected",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            # Temporarily allow multiple faces for debugging
                            # if face_count > 1:
                            #     await _mark_rejection(
                            #         image=face_input if face_input is not None else frame,
                            #         frame_index=frame_index,
                            #         reason="multiple_faces_detected",
                            #         detector_face_count=face_count,
                            #         detector_confidence=detector_confidence,
                            #         detector_bbox=detector_bbox,
                            #     )
                            #     continue

                            if _is_occluded_detection(top_detection, frame.shape):
                                await _mark_rejection(
                                    image=face_input if face_input is not None else frame,
                                    frame_index=frame_index,
                                    reason="occluded_face_detected",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            blur, brightness, contrast = _quality(face_input)
                            if blur < BLUR_THRESHOLD or brightness < LIGHT_LOW_THRESHOLD or brightness > LIGHT_HIGH_THRESHOLD:
                                await _mark_rejection(
                                    image=face_input,
                                    frame_index=frame_index,
                                    reason="face_quality_failed",
                                    detail=f"blur={blur:.1f} brightness={brightness:.1f} contrast={contrast:.1f}",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            angle = _angle(list(face_box), frame.shape)
                            angle_reached = False
                            async with state_lock:
                                angle_reached = angle_counts[angle] >= MAX_PER_ANGLE
                            if angle_reached:
                                await _mark_rejection(
                                    image=face_input,
                                    frame_index=frame_index,
                                    reason="angle_quota_reached",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            candidate_hash = _image_ahash(face_input)
                            if _is_duplicate_hash(candidate_hash, seen_hashes):
                                await _mark_rejection(
                                    image=face_input,
                                    frame_index=frame_index,
                                    reason="duplicate_frame_detected",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            ok_encode, encoded = cv2.imencode(".jpg", face_input)
                            if not ok_encode:
                                await _mark_rejection(
                                    image=face_input,
                                    frame_index=frame_index,
                                    reason="face_encode_failed",
                                    detector_face_count=face_count,
                                    detector_confidence=detector_confidence,
                                    detector_bbox=detector_bbox,
                                )
                                continue

                            sample_index = len(accepted_samples) + 1
                            if sample_index > target_frames:
                                return

                            sample_object_name = _sample_object_name(employee.name, camera.name, job_id, sample_index)
                            sample_score = round(min(1.0, (blur / 250.0)) * min(1.0, contrast / 40.0), 3)
                            stored_object_name = await asyncio.to_thread(
                                upload_employee_face,
                                sample_object_name,
                                encoded.tobytes(),
                                "image/jpeg",
                            )
                            record_current_face_preview(job_id, encoded.tobytes())
                            record_accepted_face_preview(job_id, encoded.tobytes())
                            record_image_uploaded(job_id)

                            sample_record = {
                                "frame_index": frame_index,
                                "quality_score": sample_score,
                                "angle_label": angle,
                                "sample_image_object_name": stored_object_name,
                                "face_count": face_count,
                                "detector_confidence": detector_confidence,
                                "detector_bbox": detector_bbox,
                            }
                            async with state_lock:
                                accepted_samples.append(sample_record)
                                seen_hashes.append(candidate_hash)
                                angle_counts[angle] = angle_counts.get(angle, 0) + 1
                                state["uploaded"] = len(accepted_samples)
                                state["detector_face_count"] = face_count
                                state["detector_confidence"] = detector_confidence
                                state["detector_bbox"] = detector_bbox
                                state["rejection_reason"] = None
                                state["phase"] = "uploading"
                            set_queue_depth(job_id, max(0, len(accepted_samples) - int(state["embedded"])))
                            progress_event.set()
                    finally:
                        pass

                async def _embed_sample(sample: dict[str, Any]) -> tuple[list[float], bytes]:
                    image_bytes = await asyncio.to_thread(_download_employee_face_bytes, sample["sample_image_object_name"])
                    decoded = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if decoded is None:
                        raise RuntimeError(f"Failed to decode uploaded face image: {sample['sample_image_object_name']}")
                    embedding = await _embed(decoded)
                    return embedding, image_bytes

                async def _persist_embedded_sample(sample: dict[str, Any], embedding: list[float], sample_version: int) -> None:
                    async with async_session() as persist_db:
                        template = EmployeeFaceTemplate(
                            employee_id=employee.id,
                            embedding=embedding,
                            quality_score=sample["quality_score"],
                            angle_label=sample["angle_label"],
                            source_camera_id=camera.id,
                            source_session_id=None,
                            capture_date=datetime.now(timezone.utc),
                            is_active=False,
                            version=sample_version,
                            sample_image_object_name=sample["sample_image_object_name"],
                        )
                        persist_db.add(template)
                        await persist_db.commit()

                async def _embedding_producer(embed_queue: asyncio.Queue[Any]) -> None:
                    for sample in accepted_samples:
                        await embed_queue.put(sample)
                    for _ in range(EMBEDDING_WORKERS):
                        await embed_queue.put(None)

                async def _embedding_consumer(embed_queue: asyncio.Queue[Any]) -> None:
                    nonlocal next_template_version
                    task = asyncio.current_task()
                    worker_suffix = 1
                    if task is not None:
                        try:
                            worker_suffix = int(task.get_name().split("-")[-1]) + 1
                        except Exception:
                            worker_suffix = 1
                    worker_name = f"embedding_worker_{worker_suffix}"
                    while True:
                        item = await embed_queue.get()
                        try:
                            if item is None:
                                set_worker_state(job_id, worker_name, "finished")
                                return

                            set_worker_state(job_id, worker_name, "embedding")
                            set_queue_depth(job_id, embed_queue.qsize())

                            embedding: list[float] | None = None
                            last_error: Exception | None = None
                            embed_started_at = time.monotonic()
                            for attempt in range(3):
                                try:
                                    embedding, _ = await _embed_sample(item)
                                    break
                                except Exception as exc:
                                    last_error = exc
                                    if attempt >= 2:
                                        raise
                                    await asyncio.sleep(0.5 * (attempt + 1))
                            if embedding is None:
                                raise RuntimeError(f"Failed to embed sample {item['sample_image_object_name']}: {last_error}")

                            record_embedding_timing(job_id, (time.monotonic() - embed_started_at) * 1000.0)

                            async with persist_lock:
                                next_template_version += 1
                                sample_version = next_template_version
                            await _persist_embedded_sample(item, embedding, sample_version)

                            async with state_lock:
                                state["embedded"] += 1
                                state["detector_face_count"] = int(item["face_count"])
                                state["detector_confidence"] = item["detector_confidence"]
                                state["detector_bbox"] = item["detector_bbox"]
                                state["rejection_reason"] = None
                            set_queue_depth(job_id, max(0, len(accepted_samples) - int(state["embedded"])))
                            progress_event.set()
                            set_worker_state(job_id, worker_name, "busy")
                        finally:
                            set_queue_depth(job_id, embed_queue.qsize())
                            embed_queue.task_done()
                    set_worker_state(job_id, worker_name, "finished")

                progress_task = asyncio.create_task(_progress_reporter(), name=f"face-training-progress-{job_id}")
                producer_task = asyncio.create_task(_capture_producer(), name=f"face-training-capture-{job_id}")

                try:
                    await producer_task

                    async with state_lock:
                        captured_frames = int(state["captured"])
                        rejected_frames = int(state["rejected"])
                        accepted_frame_count = len(accepted_samples)
                        final_angle_counts = dict(angle_counts)
                        final_status = str(state["phase"])

                    if await _job_cancel_requested(db, job_id) or final_status == "cancelled":
                        await db.execute(
                            update(EmployeeFaceTrainingJob)
                            .where(EmployeeFaceTrainingJob.id == job_id)
                            .values(
                                status="cancelled",
                                finished_at=datetime.now(timezone.utc),
                            )
                        )
                        return

                    if accepted_frame_count < target_frames:
                        raise HTTPException(422, f"Only {accepted_frame_count} approved samples were captured; target is {target_frames}")

                    await db.execute(
                        update(EmployeeFaceTrainingJob)
                        .where(EmployeeFaceTrainingJob.id == job_id)
                        .values(
                            status="uploading",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    async with state_lock:
                        state["phase"] = "uploading"
                    progress_event.set()

                    await db.execute(
                        update(EmployeeFaceTrainingJob)
                        .where(EmployeeFaceTrainingJob.id == job_id)
                        .values(
                            status="embedding_processing",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    async with state_lock:
                        state["phase"] = "embedding_processing"
                    progress_event.set()

                    embed_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=TRAINING_QUEUE_SIZE)
                    embedding_producer_task = asyncio.create_task(_embedding_producer(embed_queue), name=f"face-training-embedding-producer-{job_id}")
                    embedding_consumer_tasks = [
                        asyncio.create_task(_embedding_consumer(embed_queue), name=f"face-training-embedding-consumer-{job_id}-{index}")
                        for index in range(EMBEDDING_WORKERS)
                    ]

                    try:
                        await asyncio.gather(embedding_producer_task, *embedding_consumer_tasks)
                    finally:
                        for task in (embedding_producer_task, *embedding_consumer_tasks):
                            if not task.done():
                                task.cancel()
                        for task in embedding_consumer_tasks:
                            with contextlib.suppress(asyncio.CancelledError):
                                await task

                    export_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    export_object_name = (
                        f"employee-face-training/{_object_segment(employee.name)}/{_object_segment(camera.name)}/"
                        f"{job_id}/{export_timestamp}-face_embedding.pkl"
                    )
                    async with async_session() as export_db:
                        rows_r = await export_db.execute(
                            select(EmployeeFaceTemplate).where(
                                EmployeeFaceTemplate.employee_id == employee.id,
                                EmployeeFaceTemplate.sample_image_object_name.in_([sample["sample_image_object_name"] for sample in accepted_samples]),
                            )
                        )
                        rows = rows_r.scalars().all()
                    export_payload = {
                        "employee_id": str(employee.id),
                        "employee_name": employee.name,
                        "job_id": str(job_id),
                        "camera_id": str(camera.id),
                        "camera_name": camera.name,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "samples": [
                            {
                                "angle_label": row.angle_label,
                                "quality_score": float(row.quality_score),
                                "embedding": row.embedding,
                                "sample_image_object_name": row.sample_image_object_name,
                            }
                            for row in rows
                        ],
                    }
                    await asyncio.to_thread(
                        upload_employee_asset,
                        export_object_name,
                        pickle.dumps(export_payload),
                        "application/octet-stream",
                    )

                    if replace_existing:
                        await db.execute(
                            update(EmployeeFaceTemplate)
                            .where(
                                EmployeeFaceTemplate.employee_id == employee.id,
                                EmployeeFaceTemplate.is_active == True,
                                EmployeeFaceTemplate.version <= version_floor,
                            )
                            .values(is_active=False)
                        )

                    await db.execute(
                        update(EmployeeFaceTemplate)
                        .where(
                            EmployeeFaceTemplate.employee_id == employee.id,
                            EmployeeFaceTemplate.version > version_floor,
                        )
                        .values(is_active=True)
                    )

                    async with state_lock:
                        state["phase"] = "completed"
                        state["export_object_name"] = export_object_name
                        state["finished_at"] = datetime.now(timezone.utc)
                    progress_event.set()

                    await db.execute(
                        update(EmployeeFaceTrainingJob)
                        .where(EmployeeFaceTrainingJob.id == job_id)
                        .values(
                            status="completed",
                            finished_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                finally:
                    reporter_done_event.set()
                    progress_event.set()
                    progress_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await progress_task
            finally:
                await asyncio.to_thread(capture.release)

        except Exception as exc:
            logger.exception("Face training job %s failed", job_id)
            try:
                await db.rollback()
                import traceback
                async with async_session() as failure_db:
                    failure_status = await failure_db.scalar(
                        select(EmployeeFaceTrainingJob.status).where(EmployeeFaceTrainingJob.id == job_id)
                    )
                    if failure_status and failure_status not in {"completed", "cancelled"}:
                        await failure_db.execute(
                            update(EmployeeFaceTrainingJob)
                            .where(EmployeeFaceTrainingJob.id == job_id)
                            .values(
                                status="failed",
                                error_message=traceback.format_exc(limit=3).splitlines()[-1],
                                finished_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc),
                            )
                        )
                        await failure_db.commit()
            except Exception:
                logger.exception("Could not persist failure state for job %s", job_id)