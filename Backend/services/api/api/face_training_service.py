"""Employee face training workflow — guided capture, quality filtering, ArcFace enrollment."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pickle
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airco.config import settings
from airco.db import async_session
from airco.minio_client import upload_bytes, upload_employee_face
from airco.models import Camera, Employee, EmployeeFaceTemplate, EmployeeFaceTrainingJob

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

SCRFD_MODEL_PATH = Path("/models/scrfd/det_10g.onnx")
SCRFD_INPUT_SIZE = 640
SCRFD_SCORE_THRESHOLD = 0.45
SCRFD_NMS_THRESHOLD = 0.4
EMBEDDING_WORKERS = 3
TRAINING_QUEUE_SIZE = 12
PROGRESS_COMMIT_FRAME_INTERVAL = 10
PROGRESS_COMMIT_SECONDS = 2.0
SCRFD_SESSION: Any | None = None

ARCFACE_REF_POINTS = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32,
)

# ── Schemas ──────────────────────────────────────────────────────────────────

class FaceTrainingStartRequest(BaseModel):
    camera_id: uuid.UUID
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
    rejected_frames: int
    target_frames: int
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

def _valid_onnx(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        header = path.read_bytes()[:128]
    except OSError:
        return False
    if b"git-lfs.github.com/spec/v1" in header:
        return False
    try:
        import onnx; onnx.load(path); return True
    except ImportError:
        return header.startswith(b"\x08") and b"input" in header
    except Exception:
        return False


def _ensure_scrfd() -> Any | None:
    global SCRFD_SESSION
    if SCRFD_SESSION is not None:
        return SCRFD_SESSION
    if not _valid_onnx(SCRFD_MODEL_PATH):
        logger.warning("SCRFD model missing/invalid: %s", SCRFD_MODEL_PATH)
        return None
    import onnxruntime as ort
    for providers in (["CUDAExecutionProvider", "CPUExecutionProvider"], ["CPUExecutionProvider"]):
        try:
            SCRFD_SESSION = ort.InferenceSession(str(SCRFD_MODEL_PATH), providers=providers)
            logger.info("SCRFD loaded (%s)", providers[0])
            return SCRFD_SESSION
        except Exception as exc:
            logger.warning("SCRFD load failed (%s): %s", providers[0], exc)
    return None


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


def _detect_scrfd(frame: np.ndarray) -> list[dict[str, Any]]:
    sess = _ensure_scrfd()
    if sess is None:
        return []
    h, w = frame.shape[:2]
    inp = cv2.resize(frame, (SCRFD_INPUT_SIZE, SCRFD_INPUT_SIZE)).astype(np.float32).transpose(2,0,1)[np.newaxis]
    try:
        scores, bboxes, kps = _decode_scrfd(sess.run(None, {sess.get_inputs()[0].name: inp}))
    except Exception as exc:
        logger.warning("SCRFD inference failed: %s", exc); return []
    if not len(scores):
        return []
    sx, sy = w / SCRFD_INPUT_SIZE, h / SCRFD_INPUT_SIZE
    out = []
    for idx in _nms(bboxes, scores):
        b = bboxes[idx]
        bbox = [max(0., b[0]*sx), max(0., b[1]*sy), min(float(w), b[2]*sx), min(float(h), b[3]*sy)]
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
        if w >= FACE_MIN_SIZE_PX and h >= FACE_MIN_SIZE_PX:
            out.append({"bbox": [float(x), float(y), float(x+w), float(y+h)], "score": 0.5, "keypoints": None})
    return sorted(out, key=lambda d: (d["bbox"][2]-d["bbox"][0])*(d["bbox"][3]-d["bbox"][1]), reverse=True)


def _detect(frame: np.ndarray) -> list[dict[str, Any]]:
    return _detect_scrfd(frame) or _detect_haar(frame)


def _best_detection(detections: list[dict[str, Any]], frame_shape: tuple) -> dict[str, Any] | None:
    if not detections:
        return None
    fh, fw = frame_shape[:2]
    cx, cy = fw / 2.0, fh / 2.0
    return max(detections, key=lambda d: (
        d.get("score", 0.0),
        (d["bbox"][2]-d["bbox"][0]) * (d["bbox"][3]-d["bbox"][1]),
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
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    fh, fw = frame.shape[:2]
    return frame[
        max(0, int(y1 - h*margin)) : min(fh, int(y2 + h*margin)),
        max(0, int(x1 - w*margin)) : min(fw, int(x2 + w*margin)),
    ].copy()


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
    return any(x in n for x in ("invalid_protobuf", "protobuf parsing failed",
                                 "scrfd model is missing", "scrfd detector load failed"))

def _log_training_rejection(job: Any, *, frame_index: int, reason: str, detail: str | None = None) -> None:
    extra = f" detail={detail}" if detail else ""
    logger.info("Training rejection job=%s frame=%d reason=%s%s", job.id, frame_index, reason, extra)


def _log_training_debug(job: Any, *, frame_index: int, face_count: int, confidence: float | None, bbox: list[float] | None) -> None:
    logger.debug("Training debug job=%s frame=%d faces=%d conf=%s bbox=%s", job.id, frame_index, face_count, confidence, bbox)


# ── ArcFace embedding ─────────────────────────────────────────────────────────

async def _embed(face: np.ndarray) -> list[float]:
    import tritonclient.grpc.aio as grpc
    from PIL import Image
    arr = np.asarray(Image.fromarray(cv2.cvtColor(face, cv2.COLOR_BGR2RGB)).resize((112, 112)),
                     dtype=np.float32).transpose(2, 0, 1)[np.newaxis] / 255.0
    client = grpc.InferenceServerClient(url=settings.triton_url)
    inp = grpc.InferInput("input", arr.shape, "FP32")
    inp.set_data_from_numpy(arr)
    return (await client.infer("arcface", [inp])).as_numpy("output").flatten().tolist()

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
            EmployeeFaceTrainingJob.status.in_(("capturing", "processing")),
        ).order_by(EmployeeFaceTrainingJob.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


async def _set_state(db: AsyncSession, job: EmployeeFaceTrainingJob, **fields: Any) -> None:
    for k, v in fields.items():
        setattr(job, k, v)
    job.updated_at = fields.get("updated_at", datetime.now(timezone.utc))
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
        target_frames=DEFAULT_TARGET_FRAMES, duration_seconds=DEFAULT_DURATION_SECONDS,
        replace_existing=False, debug_mode=False,
        angle_coverage={l: 0 for l in ANGLE_LABELS},
        export_object_name=None, error_message=None,
        detector_face_count=0, detector_confidence=None, detector_bbox=None,
        rejection_reason=None, started_at=None, updated_at=None, finished_at=None,
    )


async def _job_payload(db: AsyncSession, *, employee: Employee, job: EmployeeFaceTrainingJob | None) -> FaceTrainingStatusResponse:
    if job is None:
        return _idle_response(employee)
    if job.status == "failed" and _is_stale_scrfd_error(job.error_message) and _ensure_scrfd():
        return _idle_response(employee)

    camera_name = None
    if job.camera_id:
        r = await db.execute(select(Camera).where(Camera.id == job.camera_id))
        cam = r.scalar_one_or_none()
        camera_name = cam.name if cam else None

    coverage = {l: int((job.angle_coverage or {}).get(l, 0) or 0) for l in ANGLE_LABELS}
    return FaceTrainingStatusResponse(
        job_id=job.id, employee_id=employee.id, employee_name=employee.name,
        camera_id=job.camera_id, camera_name=camera_name, state=job.status,
        progress=int(job.progress or 0), captured_frames=int(job.captured_frames or 0),
        accepted_frames=int(job.accepted_frames or 0), rejected_frames=int(job.rejected_frames or 0),
        target_frames=int(job.target_frames or DEFAULT_TARGET_FRAMES),
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
    replace_existing: bool, target_frames: int = DEFAULT_TARGET_FRAMES,
    duration_seconds: int = DEFAULT_DURATION_SECONDS, debug_mode: bool = False,
) -> FaceTrainingStatusResponse:
    async with async_session() as db:
        emp_r = await db.execute(select(Employee).where(Employee.id == employee_id, Employee.tenant_id == tenant_id))
        emp = emp_r.scalar_one_or_none()
        if emp is None: raise HTTPException(404, "Employee not found")

        cam_r = await db.execute(select(Camera).where(Camera.id == camera_id, Camera.tenant_id == tenant_id))
        cam = cam_r.scalar_one_or_none()
        if cam is None: raise HTTPException(404, "Camera not found")
        if not cam.is_active: raise HTTPException(409, "Selected camera is offline")

        if await _active_job(db, employee_id):
            raise HTTPException(409, "Face training already in progress for this employee")

        job = EmployeeFaceTrainingJob(
            tenant_id=tenant_id, employee_id=employee_id, camera_id=camera_id,
            status="capturing", progress=0, captured_frames=0, accepted_frames=0, rejected_frames=0,
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
        job = await db.get(EmployeeFaceTrainingJob, job_id)
        if job is None:
            return
        try:
            emp_r = await db.execute(select(Employee).where(Employee.id == job.employee_id, Employee.tenant_id == job.tenant_id))
            cam_r = await db.execute(select(Camera).where(Camera.id == job.camera_id, Camera.tenant_id == job.tenant_id))
            emp, cam = emp_r.scalar_one_or_none(), cam_r.scalar_one_or_none()
            if emp is None or cam is None:
                raise HTTPException(404, "Employee or camera not found")
            employee = emp
            camera = cam
            if job.cancel_requested:
                return

            await _set_state(db, job, status="capturing", updated_at=datetime.now(timezone.utc))

            capture = await _open_capture(_rtsp_url(cam.name))
            if capture is None:
                raise HTTPException(409, "Unable to open camera stream for training")

            version_floor = await _latest_template_version(db, employee.id)

            try:
                accepted_samples: list[dict[str, Any]] = []
                seen_embeddings: list[list[float]] = []
                angle_counts: dict[str, int] = {label: 0 for label in ANGLE_LABELS}
                state: dict[str, Any] = {
                    "processed": 0,
                    "rejected": 0,
                    "status": "capturing",
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
                shutdown_event = asyncio.Event()
                reporter_done_event = asyncio.Event()
                queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=TRAINING_QUEUE_SIZE)
                next_template_version = version_floor

                async def _snapshot() -> dict[str, Any]:
                    async with state_lock:
                        return {
                            "status": state["status"],
                            "processed": state["processed"],
                            "rejected": state["rejected"],
                            "accepted": len(accepted_samples),
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
                        progress_job = await progress_db.get(EmployeeFaceTrainingJob, job.id)
                        if progress_job is None:
                            return
                        progress_job.status = snapshot["status"]
                        progress_job.captured_frames = int(snapshot["processed"])
                        progress_job.accepted_frames = int(snapshot["accepted"])
                        progress_job.rejected_frames = int(snapshot["rejected"])
                        progress_job.progress = _progress(int(snapshot["accepted"]), job.target_frames)
                        progress_job.angle_coverage = _json_safe(snapshot["angle_coverage"])
                        progress_job.detector_face_count = int(snapshot["detector_face_count"] or 0)
                        progress_job.detector_confidence = (
                            float(snapshot["detector_confidence"]) if snapshot["detector_confidence"] is not None else None
                        )
                        progress_job.detector_bbox = _json_safe(snapshot["detector_bbox"])
                        progress_job.rejection_reason = snapshot["rejection_reason"]
                        progress_job.export_object_name = snapshot["export_object_name"]
                        progress_job.error_message = snapshot["error_message"]
                        progress_job.updated_at = datetime.now(timezone.utc)
                        progress_job.finished_at = snapshot["finished_at"]
                        await progress_db.commit()

                async def _persist_accepted_sample(sample: dict[str, Any]) -> int:
                    nonlocal next_template_version

                    async with persist_lock:
                        next_template_version += 1
                        sample_version = next_template_version

                    sample_object_name = await asyncio.to_thread(
                        upload_employee_face,
                        sample["sample_image_object_name"],
                        sample["image_bytes"],
                        "image/jpeg",
                    )

                    async with async_session() as persist_db:
                        template = EmployeeFaceTemplate(
                            employee_id=employee.id,
                            embedding=sample["embedding"],
                            quality_score=sample["quality_score"],
                            angle_label=sample["angle_label"],
                            source_camera_id=camera.id,
                            source_session_id=None,
                            capture_date=datetime.now(timezone.utc),
                            is_active=False,
                            version=sample_version,
                            sample_image_object_name=sample_object_name,
                        )
                        persist_db.add(template)
                        await persist_db.commit()

                    return sample_version

                async def _progress_reporter() -> None:
                    last_committed_processed = 0
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
                            snapshot["processed"] - last_committed_processed >= PROGRESS_COMMIT_FRAME_INTERVAL
                            or now - last_committed_at >= PROGRESS_COMMIT_SECONDS
                            or reporter_done_event.is_set()
                        )
                        if not should_commit:
                            continue
                        await _persist_snapshot(snapshot)
                        last_committed_processed = snapshot["processed"]
                        last_committed_at = now
                        if reporter_done_event.is_set():
                            return

                async def _producer() -> None:
                    nonlocal angle_counts
                    last_failure_count = 0
                    start = time.monotonic()
                    try:
                        while True:
                            await db.refresh(job)
                            if job.cancel_requested:
                                async with state_lock:
                                    state["status"] = "cancelled"
                                    state["finished_at"] = datetime.now(timezone.utc)
                                shutdown_event.set()
                                progress_event.set()
                                return

                            now = time.monotonic()
                            async with state_lock:
                                accepted_count = len(accepted_samples)
                            if now - start >= job.duration_seconds:
                                shutdown_event.set()
                                return
                            if accepted_count >= job.target_frames:
                                shutdown_event.set()
                                return

                            ok, frame = await _read_frame(capture)
                            if not ok or frame is None:
                                last_failure_count += 1
                                _log_training_rejection(
                                    job,
                                    frame_index=state["processed"],
                                    reason="camera_frame_unavailable",
                                    detail=f"failure_count={last_failure_count}",
                                )
                                async with state_lock:
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
                                state["processed"] += 1
                                frame_index = state["processed"]

                            detections = await asyncio.to_thread(_detect, frame)
                            face_count = len(detections)
                            top_detection = _best_detection(detections, frame.shape)
                            detector_confidence = float(top_detection["score"]) if top_detection else None
                            detector_bbox = _normalize_bbox(top_detection["bbox"], frame.shape) if top_detection else None

                            if job.debug_mode:
                                _log_training_debug(
                                    job,
                                    frame_index=frame_index,
                                    face_count=face_count,
                                    confidence=detector_confidence,
                                    bbox=detector_bbox,
                                )

                            if face_count == 0 or top_detection is None:
                                _log_training_rejection(job, frame_index=frame_index, reason="no_face_detected")
                                async with state_lock:
                                    state["rejected"] += 1
                                    state["detector_face_count"] = face_count
                                    state["detector_confidence"] = detector_confidence
                                    state["detector_bbox"] = detector_bbox
                                    state["rejection_reason"] = "no_face_detected"
                                progress_event.set()
                                continue

                            face_box = tuple(int(value) for value in top_detection["bbox"])
                            face_crop = _crop(frame, list(face_box))
                            keypoints = top_detection.get("keypoints")
                            aligned_face = _align(frame, keypoints) if keypoints is not None else None
                            face_input = aligned_face if aligned_face is not None else face_crop

                            blur, brightness, contrast = _quality(face_input)
                            if blur < BLUR_THRESHOLD or brightness < LIGHT_LOW_THRESHOLD or brightness > LIGHT_HIGH_THRESHOLD:
                                _log_training_rejection(
                                    job,
                                    frame_index=frame_index,
                                    reason="face_quality_failed",
                                    detail=f"blur={blur:.1f} brightness={brightness:.1f} contrast={contrast:.1f}",
                                )
                                async with state_lock:
                                    state["rejected"] += 1
                                    state["detector_face_count"] = face_count
                                    state["detector_confidence"] = detector_confidence
                                    state["detector_bbox"] = detector_bbox
                                    state["rejection_reason"] = "face_quality_failed"
                                progress_event.set()
                                continue

                            angle = _angle(list(face_box), frame.shape)
                            ok_encode, encoded = cv2.imencode(".jpg", face_input)
                            if not ok_encode:
                                _log_training_rejection(job, frame_index=frame_index, reason="face_encode_failed")
                                async with state_lock:
                                    state["rejected"] += 1
                                    state["detector_face_count"] = face_count
                                    state["detector_confidence"] = detector_confidence
                                    state["detector_bbox"] = detector_bbox
                                    state["rejection_reason"] = "face_encode_failed"
                                progress_event.set()
                                continue

                            sample_index = 0
                            sample_object_name = ""
                            sample_score = round(min(1.0, (blur / 250.0)) * min(1.0, contrast / 40.0), 3)
                            async with state_lock:
                                if len(accepted_samples) < job.target_frames:
                                    sample_index = len(accepted_samples) + 1
                                    sample_object_name = f"employee-face-training/{employee.id}/{job.id}/{sample_index:03d}-{angle}.jpg"
                                state["detector_face_count"] = face_count
                                state["detector_confidence"] = detector_confidence
                                state["detector_bbox"] = detector_bbox
                                state["rejection_reason"] = None
                            if sample_index == 0:
                                continue

                            await queue.put(
                                {
                                    "frame_index": frame_index,
                                    "face_input": face_input,
                                    "quality_score": sample_score,
                                    "angle_label": angle,
                                    "sample_image_object_name": sample_object_name,
                                    "image_bytes": encoded.tobytes(),
                                    "face_count": face_count,
                                    "detector_confidence": detector_confidence,
                                    "detector_bbox": detector_bbox,
                                }
                            )
                            progress_event.set()
                    finally:
                        shutdown_event.set()
                        for _ in range(EMBEDDING_WORKERS):
                            await queue.put(None)

                async def _consumer() -> None:
                    while True:
                        item = await queue.get()
                        try:
                            if item is None:
                                return

                            async with state_lock:
                                if len(accepted_samples) >= job.target_frames or shutdown_event.is_set():
                                    continue

                            embedding = await _embed(item["face_input"])

                            rejection_reason: str | None = None
                            rejection_detail: str | None = None
                            should_accept = False
                            async with state_lock:
                                if len(accepted_samples) >= job.target_frames or shutdown_event.is_set():
                                    continue
                                if any(_cosine(embedding, existing) > DUPLICATE_SIMILARITY_THRESHOLD for existing in seen_embeddings):
                                    state["rejected"] += 1
                                    state["rejection_reason"] = "duplicate_embedding_detected"
                                    rejection_reason = "duplicate_embedding_detected"
                                    rejection_detail = f"threshold={DUPLICATE_SIMILARITY_THRESHOLD}"
                                elif angle_counts[item["angle_label"]] >= MAX_PER_ANGLE:
                                    state["rejected"] += 1
                                    state["rejection_reason"] = "angle_quota_reached"
                                    rejection_reason = "angle_quota_reached"
                                    rejection_detail = f"angle={item['angle_label']} count={angle_counts[item['angle_label']]} max={MAX_PER_ANGLE}"
                                else:
                                    accepted_samples.append(
                                        {
                                            "embedding": embedding,
                                            "quality_score": item["quality_score"],
                                            "angle_label": item["angle_label"],
                                            "sample_image_object_name": item["sample_image_object_name"],
                                            "image_bytes": item["image_bytes"],
                                        }
                                    )
                                    seen_embeddings.append(embedding)
                                    angle_counts[item["angle_label"]] = angle_counts.get(item["angle_label"], 0) + 1
                                    state["rejection_reason"] = None
                                    should_accept = True
                                    if len(accepted_samples) >= job.target_frames:
                                        shutdown_event.set()
                                state["detector_face_count"] = int(item["face_count"])
                                state["detector_confidence"] = item["detector_confidence"]
                                state["detector_bbox"] = item["detector_bbox"]
                            if rejection_reason is not None:
                                _log_training_rejection(
                                    job,
                                    frame_index=item["frame_index"],
                                    reason=rejection_reason,
                                    detail=rejection_detail,
                                )
                            if should_accept:
                                await _persist_accepted_sample(item)
                                progress_event.set()
                        finally:
                            queue.task_done()

                progress_task = asyncio.create_task(_progress_reporter(), name=f"face-training-progress-{job.id}")
                producer_task = asyncio.create_task(_producer(), name=f"face-training-producer-{job.id}")
                consumer_tasks = [
                    asyncio.create_task(_consumer(), name=f"face-training-consumer-{job.id}-{index}")
                    for index in range(EMBEDDING_WORKERS)
                ]

                try:
                    await asyncio.gather(producer_task, *consumer_tasks)
                finally:
                    reporter_done_event.set()
                    progress_event.set()
                    progress_task.cancel()
                    for task in (producer_task, *consumer_tasks):
                        if not task.done():
                            task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await progress_task
                    for task in consumer_tasks:
                        with contextlib.suppress(asyncio.CancelledError):
                            await task

                async with state_lock:
                    captured_frames = state["processed"]
                    rejected_frames = state["rejected"]
                    accepted_frame_count = len(accepted_samples)
                    final_angle_counts = dict(angle_counts)
                    final_status = state["status"]

                if job.cancel_requested or final_status == "cancelled":
                    await _set_state(db, job, status="cancelled", finished_at=datetime.now(timezone.utc))
                    return

                if not accepted_samples:
                    raise HTTPException(422, "No high-quality face samples were captured")

                await _set_state(db, job, status="processing", updated_at=datetime.now(timezone.utc))

                export_object_name = f"employee-face-training/{employee.id}/{job.id}/face_embedding.pkl"
                export_payload = {
                    "employee_id": str(employee.id),
                    "employee_name": employee.name,
                    "job_id": str(job.id),
                    "camera_id": str(camera.id),
                    "camera_name": camera.name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "samples": [
                        {
                            "angle_label": sample["angle_label"],
                            "quality_score": sample["quality_score"],
                            "embedding": sample["embedding"],
                            "sample_image_object_name": sample["sample_image_object_name"],
                        }
                        for sample in accepted_samples
                    ],
                }
                await asyncio.to_thread(
                    upload_bytes,
                    export_object_name,
                    pickle.dumps(export_payload),
                    "application/octet-stream",
                )

                if job.replace_existing:
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

                await _set_state(db, job, status="completed", finished_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
                job.progress = 100
                job.captured_frames = captured_frames
                job.accepted_frames = accepted_frame_count
                job.rejected_frames = rejected_frames
                job.angle_coverage = final_angle_counts
                job.export_object_name = export_object_name
                await db.commit()
            finally:
                await asyncio.to_thread(capture.release)

        except Exception as exc:
            logger.exception("Face training job %s failed", job_id)
            try:
                job = await db.get(EmployeeFaceTrainingJob, job_id)
                if job and job.status not in {"completed", "cancelled"}:
                    import traceback
                    job.status = "failed"
                    job.error_message = traceback.format_exc(limit=3).splitlines()[-1]
                    job.finished_at = job.updated_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                logger.exception("Could not persist failure state for job %s", job_id)