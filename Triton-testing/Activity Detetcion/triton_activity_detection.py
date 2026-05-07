# -*- coding: utf-8 -*-
"""Robust Activity Detection using Triton inference.

This script implements the same logic as the original ``RobustActivityDetector``
but replaces the local YOLO model loads with calls to a Triton inference server.
It expects two models to be available on Triton:

* ``person_detection`` – a YOLO‑style detector that outputs ``[x, y, w, h, conf, class]``
* ``yolo26-pose``      – a pose model that outputs ``[x, y, w, h, conf, class, 17*3 keypoints]``

Both models should be configured with the same input preprocessing as the
original Ultralytics YOLO models (640×640, normalized to 0‑1, NCHW).
"""

import cv2
import time
import queue
import threading
import numpy as np
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple, Any

import tritonclient.grpc as grpcclient
from tritonclient.utils import np_to_triton_dtype

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
TRITON_URL = "localhost:8001"
RTSP_URL   = "rtsp://admin:Vijay%405458@airco-office.ddns.net:8554/Streaming/Channels/401"

# Model names on Triton (must match the model repository names)
PERSON_MODEL = "yolo26"
POSE_MODEL   = "yolo26-pose"

PERSON_CONF = 0.40
POSE_CONF   = 0.35

DISPLAY_W, DISPLAY_H = 1280, 720

# ── Activity thresholds ──────────────────────
MOVEMENT_NOISE_GATE = 0.5   # px – below this = camera shake, ignored
WORKING_THRESHOLD   = 3.5   # avg px over window to call "working"
HISTORY_SECONDS     = 10    # rolling window length in seconds
IDLE_CONFIRM_SEC    = 0     # instant flip to working, no delay for idle

# ── Track stability ──────────────────────────
TRACK_TIMEOUT_SEC = 8.0
REID_DISTANCE_PX  = 120

# Logging configuration
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
KP_NOSE        = 0
KP_L_EYE       = 1
KP_R_EYE       = 2
KP_L_EAR       = 3
KP_R_EAR       = 4
KP_L_SHOULDER  = 5
KP_R_SHOULDER  = 6
KP_L_ELBOW     = 7
KP_R_ELBOW     = 8
KP_L_WRIST     = 9
KP_R_WRIST     = 10

def _center(bbox: List[float]) -> Tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)

def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

# ─────────────────────────────────────────────
#  TRITON INFERENCE CLIENT
# ─────────────────────────────────────────────
class TritonInferenceClient:
    """Thin wrapper around Triton gRPC for the two YOLO models.

    The preprocessing mirrors the Ultralytics YOLO pipeline:
    * Resize while keeping aspect ratio
    * Pad to 640×640 with a constant value (114)
    * Convert to NCHW and normalise to [0, 1]
    """

    def __init__(self, url: str = TRITON_URL):
        log.info("Connecting to Triton at %s", url)
        self.person_client = grpcclient.InferenceServerClient(url=url, verbose=False)
        self.pose_client   = grpcclient.InferenceServerClient(url=url, verbose=False)
        log.info("Triton client ready")

    @staticmethod
    def _preprocess(frame: np.ndarray, input_size: int = 640) -> Tuple[np.ndarray, float, int, int]:
        h, w = frame.shape[:2]
        scale = min(input_size / h, input_size / w)
        new_h, new_w = int(h * scale), int(w * scale)
        resized = cv2.resize(frame, (new_w, new_h))
        pad_h = input_size - new_h
        pad_w = input_size - new_w
        padded = cv2.copyMakeBorder(resized, 0, pad_h, 0, pad_w,
                                   cv2.BORDER_CONSTANT, value=(114, 114, 114))
        tensor = padded.astype(np.float32) / 255.0
        tensor = tensor.transpose(2, 0, 1)  # HWC -> CHW
        tensor = np.expand_dims(tensor, axis=0)  # batch dim
        return tensor, scale, pad_w, pad_h

    def _run_inference(self, client: grpcclient.InferenceServerClient,
                       model_name: str, input_tensor: np.ndarray) -> np.ndarray:
        inputs = [grpcclient.InferInput("images", input_tensor.shape,
                                      np_to_triton_dtype(input_tensor.dtype))]
        inputs[0].set_data_from_numpy(input_tensor)
        outputs = [grpcclient.InferRequestedOutput("output0")]
        response = client.infer(model_name=model_name, inputs=inputs, outputs=outputs)
        out = response.as_numpy("output0")
        return out.squeeze()

    def infer_person_detection(self, frame: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
        """Return a list of (x1, y1, x2, y2, conf) for detected persons.
        The Triton model is expected to output either:
        * shape (5, N) – transposed to (N, 5)
        * shape (N, 6) – [x, y, w, h, conf, class]
        Only detections with ``class == 0`` (person) and confidence > ``PERSON_CONF`` are kept.
        """
        tensor, scale, pad_w, pad_h = self._preprocess(frame)
        out = self._run_inference(self.person_client, PERSON_MODEL, tensor)
        boxes: List[Tuple[int, int, int, int, float]] = []
        if out.ndim == 2:
            # Handle both possible layouts
            if out.shape[0] == 5 and out.shape[1] > 5:  # (5, N)
                out = out.T
            if out.shape[1] == 5:  # (N, 5) – no class column
                for det in out:
                    x, y, w, h, conf = det
                    if conf < PERSON_CONF:
                        continue
                    x1 = (x - w / 2 - pad_w) / scale
                    y1 = (y - h / 2 - pad_h) / scale
                    x2 = (x + w / 2 - pad_w) / scale
                    y2 = (y + h / 2 - pad_h) / scale
                    boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
            elif out.shape[1] == 6:  # (N, 6) includes class
                for det in out:
                    x, y, w, h, conf, cls = det
                    if int(cls) != 0 or conf < PERSON_CONF:
                        continue
                    x1 = (x - w / 2 - pad_w) / scale
                    y1 = (y - h / 2 - pad_h) / scale
                    x2 = (x + w / 2 - pad_w) / scale
                    y2 = (y + h / 2 - pad_h) / scale
                    boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
        return boxes

    def infer_pose_detection(self, frame: np.ndarray) -> List[Tuple[Tuple[int, int, int, int], np.ndarray, float]]:
        """Return a list of (bbox, keypoints, conf) for each detected person.
        ``keypoints`` is a ``(17, 3)`` array ``[x, y, confidence]``.
        """
        tensor, scale, pad_w, pad_h = self._preprocess(frame)
        out = self._run_inference(self.pose_client, POSE_MODEL, tensor)
        persons: List[Tuple[Tuple[int, int, int, int], np.ndarray, float]] = []
        if out.ndim == 2 and out.shape[1] == 57:
            for det in out:
                x, y, w, h, conf, cls = det[:6]
                if int(cls) != 0 or conf < POSE_CONF:
                    continue
                x1 = (x - w / 2 - pad_w) / scale
                y1 = (y - h / 2 - pad_h) / scale
                x2 = (x + w / 2 - pad_w) / scale
                y2 = (y + h / 2 - pad_h) / scale
                kp_raw = det[6:]
                keypoints = kp_raw.reshape(17, 3).copy()
                keypoints[:, 0] = (keypoints[:, 0] - pad_w) / scale
                keypoints[:, 1] = (keypoints[:, 1] - pad_h) / scale
                persons.append(((int(x1), int(y1), int(x2), int(y2)), keypoints, float(conf)))
        elif out.ndim == 2 and out.shape[1] == 6:
            # Pose model without keypoints – treat as bbox only
            for det in out:
                x, y, w, h, conf, cls = det
                if int(cls) != 0 or conf < POSE_CONF:
                    continue
                x1 = (x - w / 2 - pad_w) / scale
                y1 = (y - h / 2 - pad_h) / scale
                x2 = (x + w / 2 - pad_w) / scale
                y2 = (y + h / 2 - pad_h) / scale
                persons.append(((int(x1), int(y1), int(x2), int(y2)), None, float(conf)))
        return persons

# ─────────────────────────────────────────────
#  PERSON TRACK CLASS (unchanged from original script)
# ─────────────────────────────────────────────
class PersonTrack:
    @staticmethod
    def fmt(seconds: float) -> str:
        """Format seconds as HH:MM:SS string."""
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, s2 = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s2:02d}"

    _next_display_id = 1
    _id_map: Dict[int, int] = {}

    def __init__(self, yolo_id: int):
        self.yolo_id = yolo_id
        if yolo_id not in PersonTrack._id_map:
            PersonTrack._id_map[yolo_id] = PersonTrack._next_display_id
            PersonTrack._next_display_id += 1
        self.display_id = PersonTrack._id_map[yolo_id]
        self.state = "unknown"
        self.movement_history: deque = deque()
        self.working_time = 0.0
        self.idle_time = 0.0
        self._state_start = time.time()
        self._low_move_since: Optional[float] = None
        self.last_center: Optional[Tuple[float, float]] = None
        self.last_seen = time.time()
        self.bbox = [0.0, 0.0, 0.0, 0.0]
        self.last_raw_move = 0.0

    def update_movement(self, center: Tuple[float, float], ts: float):
        if self.last_center is not None:
            dx = center[0] - self.last_center[0]
            dy = center[1] - self.last_center[1]
            raw = (dx ** 2 + dy ** 2) ** 0.5
            self.last_raw_move = raw
            score = raw if raw > MOVEMENT_NOISE_GATE else 0.0
            self.movement_history.append((ts, score))
        self.last_center = center
        self.last_seen = ts
        cutoff = ts - HISTORY_SECONDS
        while self.movement_history and self.movement_history[0][0] < cutoff:
            self.movement_history.popleft()

    @property
    def avg_movement(self) -> float:
        if not self.movement_history:
            return 0.0
        return float(np.mean([s for _, s in self.movement_history]))

    def update_state(self, ts: float):
        avg = self.avg_movement
        new_state = "working" if avg > WORKING_THRESHOLD else "idle"
        if new_state != self.state:
            self._commit(new_state, ts)

    def _commit(self, new_state: str, ts: float):
        if self.state != "unknown":
            elapsed = ts - self._state_start
            if self.state == "working":
                self.working_time += elapsed
            else:
                self.idle_time += elapsed
        self._state_start = ts
        log.info(f"Person {self.display_id}: {self.state} -> {new_state} (avg={self.avg_movement:.2f}px)")
        self.state = new_state

    def flush_timers(self) -> Tuple[float, float]:
        now = time.time()
        elapsed = now - self._state_start
        if self.state == "working":
            return self.working_time + elapsed, self.idle_time
        else:
            return self.working_time, self.idle_time + elapsed

# ─────────────────────────────────────────────
#  DETECTOR CLASS (uses Triton client)
# ─────────────────────────────────────────────
class RobustActivityDetector:
    def __init__(self):
        log.info("Initializing Triton inference client …")
        self.triton = TritonInferenceClient()
        self.tracks: Dict[int, PersonTrack] = {}
        self.frame_no = 0

    def process_frame(self, frame: np.ndarray) -> Dict:
        self.frame_no += 1
        now = time.time()
        # 1️⃣ Person detection via Triton
        person_boxes = self.triton.infer_person_detection(frame)
        # 2️⃣ Pose detection via Triton (keypoints)
        pose_results = self.triton.infer_pose_detection(frame)
        # Build a simple list of (bbox, keypoints) for matching
        persons = []
        for pbox, kps, conf in pose_results:
            persons.append((pbox, kps, conf))
        # 3️⃣ Match phones – not needed here, we just track persons
        for pbox in person_boxes:
            pc = _center(pbox[:4])
            # Find existing track or create new one
            best_id = -1
            best_dist = float('inf')
            for tid, track in self.tracks.items():
                # Simple Euclidean distance between centers
                dist = _dist(pc, _center(track.bbox))
                if dist < best_dist:
                    best_dist = dist
                    best_id = tid
            if best_id == -1 or best_dist > REID_DISTANCE_PX:
                # New track
                new_id = max(self.tracks.keys(), default=0) + 1
                self.tracks[new_id] = PersonTrack(new_id)
                track = self.tracks[new_id]
                log.info(f"New track: Person {track.display_id}")
            else:
                track = self.tracks[best_id]
            # Update track state
            track.bbox = list(pbox[:4])
            track.update_movement(pc, now)
            track.update_state(now)
        # Cleanup stale tracks
        stale = [tid for tid, tr in self.tracks.items() if now - tr.last_seen > TRACK_TIMEOUT_SEC]
        for tid in stale:
            tr = self.tracks.pop(tid)
            w, i = tr.flush_timers()
            log.info(f"Person {tr.display_id} expired | Work={PersonTrack.fmt(w)} Idle={PersonTrack.fmt(i)}")
        # Build result dict
        track_info = []
        working = idle = 0
        for tr in self.tracks.values():
            w, i = tr.flush_timers()
            track_info.append({
                "display_id": tr.display_id,
                "state": tr.state,
                "avg_movement": round(tr.avg_movement, 2),
                "working_time": w,
                "idle_time": i,
                "bbox": tr.bbox,
            })
            if tr.state == "working":
                working += 1
            else:
                idle += 1
        return {
            "frame_no": self.frame_no,
            "n_tracks": len(self.tracks),
            "working": working,
            "idle": idle,
            "tracks": track_info,
        }

# ─────────────────────────────────────────────
#  DRAWING UTILITIES (same visualisation as original)
# ─────────────────────────────────────────────
def draw(frame: np.ndarray, results: Dict, fps: int) -> np.ndarray:
    disp = frame.copy()
    H, W = disp.shape[:2]
    cv2.rectangle(disp, (0, 0), (W, 90), (15, 15, 15), -1)
    cv2.putText(disp, time.strftime("%Y-%m-%d %H:%M:%S"), (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 160), 1)
    cv2.putText(disp,
                f"Tracks: {results['n_tracks']}   Working: {results['working']}   Idle: {results['idle']}   Frame: {results['frame_no']}",
                (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(disp, f"FPS: {fps}", (W-115, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 80), 2)
    for t in results["tracks"]:
        x1, y1, x2, y2 = map(int, t["bbox"])
        color = (0, 210, 0) if t["state"] == "working" else (0, 0, 220)
        cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
        label = f"Person {t['display_id']} [{t['state'].upper()}]"
        cv2.putText(disp, label, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return disp

# ─────────────────────────────────────────────
#  THREADED PIPELINE
# ─────────────────────────────────────────────
frame_q  = queue.Queue(maxsize=3)
result_q = queue.Queue(maxsize=3)
running  = True

def capture_fn():
    global running
    while running:
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
        if not cap.isOpened():
            log.error("RTSP open failed, retry in 5s …")
            time.sleep(5)
            continue
        log.info("RTSP stream opened")
        while running:
            ret, frame = cap.read()
            if not ret:
                log.warning("Frame read failed, reconnecting …")
                break
            frame = cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
            if frame_q.full():
                try: frame_q.get_nowait()
                except queue.Empty: pass
            try: frame_q.put_nowait(frame)
            except queue.Full: pass
        cap.release()

def process_fn(detector: RobustActivityDetector):
    global running
    while running:
        try:
            frame = frame_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            res = detector.process_frame(frame)
            if result_q.full():
                try: result_q.get_nowait()
                except queue.Empty: pass
            result_q.put_nowait((frame, res))
        except Exception as e:
            log.error(f"Processing error: {e}", exc_info=True)

def main():
    global running
    log.info("Robust Activity Detector (Triton) v3 – final")
    detector = RobustActivityDetector()
    t_cap  = threading.Thread(target=capture_fn, daemon=True)
    t_proc = threading.Thread(target=process_fn, args=(detector,), daemon=True)
    t_cap.start(); t_proc.start()
    fps_count, fps_val, last_fps = 0, 0, time.time()
    try:
        while True:
            try:
                raw_frame, results = result_q.get(timeout=1.0)
            except queue.Empty:
                continue
            fps_count += 1
            now = time.time()
            if now - last_fps >= 1.0:
                fps_val = fps_count
                fps_count = 0
                last_fps = now
            disp = draw(raw_frame, results, fps_val)
            cv2.imshow('Robust Activity Detection (Triton)', disp)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
    finally:
        running = False
        t_cap.join(timeout=3)
        t_proc.join(timeout=3)
        cv2.destroyAllWindows()
        log.info("Stopped.")

if __name__ == "__main__":
    main()
