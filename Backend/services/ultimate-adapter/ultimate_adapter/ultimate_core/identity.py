from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .motion import create_ukf, ukf_correct, ukf_predict
from .utils import bbox_center, bbox_iou, cosine_sim, l2_normalize


@dataclass
class PersonIdentity:
    global_id: int
    seed_embedding: np.ndarray
    ema_embedding: np.ndarray
    viewpoint_bank: deque
    viewpoint_cameras: deque = field(default_factory=lambda: deque(maxlen=50))
    color_signature: Optional[np.ndarray] = None

    birth_frame: int = -1
    last_seen_frame: int = -1
    last_bbox: Tuple = (0, 0, 0, 0)
    last_center: Tuple[float, float] = (0.0, 0.0)
    center_history: deque = field(default_factory=lambda: deque(maxlen=12))
    velocity: Tuple[float, float] = (0.0, 0.0)
    active_track_id: Optional[int] = None
    last_track_key: Optional[Tuple[str, int]] = None

    ukf_filter: Optional[object] = None
    ukf_initialized: bool = False

    birth_camera: str = "1"
    last_camera: str = "1"
    last_seen_time: float = field(default_factory=time.time)

    total_detections: int = 0
    detection_history: deque = field(default_factory=lambda: deque(maxlen=100))
    last_match_score: float = 0.0
    lock_until_frame: int = -1

    is_occluded: bool = False
    occlusion_start_frame: int = -1
    predicted_bbox: Optional[Tuple] = None

    appearance_change_scores: deque = field(default_factory=lambda: deque(maxlen=10))

    def __post_init__(self):
        if isinstance(self.viewpoint_bank, list):
            self.viewpoint_bank = deque(self.viewpoint_bank, maxlen=50)
        if isinstance(self.viewpoint_cameras, list):
            self.viewpoint_cameras = deque(self.viewpoint_cameras, maxlen=50)
        if not self.viewpoint_cameras and self.viewpoint_bank:
            self.viewpoint_cameras = deque([self.last_camera] * len(self.viewpoint_bank), maxlen=50)
        if self.last_bbox != (0, 0, 0, 0):
            self.center_history.append(self.last_center)

    def init_ukf(self, center: Tuple[float, float], cfg: dict):
        self.ukf_filter = create_ukf(center, cfg)
        self.ukf_initialized = True

    def update_ema_simple(self, new_embedding: np.ndarray, alpha: float):
        new_embedding = l2_normalize(new_embedding)
        self.ema_embedding = l2_normalize(alpha * self.ema_embedding + (1 - alpha) * new_embedding)

    def add_viewpoint(self, embedding: np.ndarray, camera_id: Optional[str] = None):
        emb = l2_normalize(embedding)
        if self.viewpoint_bank:
            if cosine_sim(emb, self.viewpoint_bank[-1]) >= 0.95:
                return
        self.viewpoint_bank.append(emb)
        self.viewpoint_cameras.append(str(camera_id or self.last_camera))

    def update_motion(self, bbox: Tuple, frame_idx: int, cfg: dict):
        center = bbox_center(bbox)
        if len(self.center_history) > 0 and frame_idx % 2 == 0:
            prev_center = self.center_history[-1]
            dt = max(1, frame_idx - self.last_seen_frame)
            self.velocity = ((center[0] - prev_center[0]) / dt, (center[1] - prev_center[1]) / dt)
        self.last_center = center
        self.last_bbox = bbox
        self.last_seen_frame = frame_idx
        self.last_seen_time = time.time()
        self.total_detections += 1
        self.center_history.append(center)

        if not self.ukf_initialized:
            self.init_ukf(center, cfg)
        elif frame_idx % 2 == 0:
            ukf_correct(self.ukf_filter, center)

    def predicted_center_ukf(self, gap_frames: int = 1) -> Tuple[float, float]:
        if gap_frames <= 2 or not self.ukf_initialized:
            return (self.last_center[0] + self.velocity[0] * gap_frames, self.last_center[1] + self.velocity[1] * gap_frames)
        try:
            return ukf_predict(self.ukf_filter)
        except Exception:
            return (self.last_center[0] + self.velocity[0] * gap_frames, self.last_center[1] + self.velocity[1] * gap_frames)

    def predict_bbox(self, gap_frames: int = 1) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.last_bbox
        px, py = self.predicted_center_ukf(gap_frames)
        cx0, cy0 = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dx, dy = px - cx0, py - cy0
        return (int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy))

    def motion_similarity(self, bbox: Tuple, gap_frames: int, sigma_px: float) -> float:
        px, py = self.predicted_center_ukf(gap_frames)
        cx, cy = bbox_center(bbox)
        dist = float(np.hypot(cx - px, cy - py))
        return float(np.exp(-(dist * dist) / max(2.0 * sigma_px * sigma_px, 1.0)))

    def get_best_match_score(self, query_embedding: np.ndarray, query_camera: Optional[str] = None) -> float:
        query = l2_normalize(query_embedding)
        scored = []

        def camera_weight(source_camera: Optional[str]) -> float:
            if source_camera is None:
                return 0.85
            if query_camera is not None and str(source_camera) == str(query_camera):
                return 1.0
            if str(source_camera) == str(self.last_camera):
                return 0.9
            return 0.8

        scored.append(cosine_sim(query, self.seed_embedding) * camera_weight(self.birth_camera))
        scored.append(cosine_sim(query, self.ema_embedding) * camera_weight(self.last_camera))
        viewpoint_cameras = list(self.viewpoint_cameras)
        for idx, vp_emb in enumerate(self.viewpoint_bank):
            cam = viewpoint_cameras[idx] if idx < len(viewpoint_cameras) else self.last_camera
            scored.append(cosine_sim(query, vp_emb) * camera_weight(cam))
        if not scored:
            return 0.0
        scored.sort(reverse=True)
        topk = scored[: min(7, len(scored))]
        weights = np.linspace(1.0, 0.6, num=len(topk), dtype=np.float32)
        return float(np.average(topk, weights=weights))

    def compute_fused_similarity(self, query_deep: np.ndarray, query_color: np.ndarray, cfg: dict, query_camera: Optional[str] = None) -> float:
        deep_sim = self.get_best_match_score(query_deep, query_camera=query_camera)
        color_sim = 0.0
        if self.color_signature is not None and query_color is not None:
            color_sim = cosine_sim(query_color, self.color_signature)
        return (cfg["deep_feature_weight"] * deep_sim + cfg["color_feature_weight"] * color_sim)

    def merge_from(self, other: "PersonIdentity") -> None:
        self.viewpoint_bank.extend(other.viewpoint_bank)
        self.viewpoint_cameras.extend(other.viewpoint_cameras)
        if other.color_signature is not None:
            if self.color_signature is None:
                self.color_signature = other.color_signature.copy()
            else:
                self.color_signature = l2_normalize(0.5 * self.color_signature + 0.5 * other.color_signature)
        self.total_detections = max(self.total_detections, other.total_detections)
        self.last_seen_frame = max(self.last_seen_frame, other.last_seen_frame)
        if other.last_seen_time > self.last_seen_time:
            self.last_seen_time = other.last_seen_time
        if other.last_bbox != (0, 0, 0, 0):
            self.last_bbox = other.last_bbox
            self.last_center = other.last_center
        self.is_occluded = self.is_occluded and other.is_occluded
        self.appearance_change_scores.extend(other.appearance_change_scores)

