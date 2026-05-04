from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

import numpy as np

from .birth import BirthCertificateSystem
from .config import UltimateCoreConfig, coerce_core_config
from .features import MultiScalePyramidExtractor, RobustFeatureExtractor
from .registry import GlobalIdentityRegistry
from .utils import bbox_center


def resolve_device(requested: str) -> str:
    requested = (requested or "cpu").strip().lower()
    if not requested.startswith("cuda"):
        return requested
    try:
        import torch

        if not torch.cuda.is_available():
            return "cpu"
        if ":" in requested:
            idx = int(requested.split(":", 1)[1])
            if idx < 0 or idx >= torch.cuda.device_count():
                return "cpu"
            return f"cuda:{idx}"
        return "cuda:0"
    except Exception:
        return "cpu"


def load_detector_model(model_path: str, device: str):
    from ultralytics import YOLO

    model_path = str(model_path)
    suffix = Path(model_path).suffix.lower()
    model = YOLO(model_path)
    if suffix not in {".onnx", ".engine"}:
        try:
            model.to(device)
        except Exception:
            pass
    return model


class UltimateStableTrackerV2:
    def __init__(
        self,
        cfg: Mapping[str, Any] | UltimateCoreConfig,
        camera_id: str = "1",
        *,
        detector=None,
        tracker_backend=None,
        feature_extractor=None,
        registry: Optional[GlobalIdentityRegistry] = None,
    ):
        self.cfg = coerce_core_config(cfg)
        self.camera_id = camera_id
        self.device = resolve_device(self.cfg["device"])
        upgrades = []
        try:
            from .motion import HAS_NX, HAS_TORCH, HAS_UKF
        except Exception:
            HAS_TORCH = HAS_NX = HAS_UKF = False
        if self.cfg.get("use_ukf") and HAS_UKF:
            upgrades.append("UKF")
        if self.cfg.get("use_transformer_motion") and HAS_TORCH:
            upgrades.append("TF-Motion")
        if self.cfg.get("use_temporal_consistency") and HAS_TORCH:
            upgrades.append("TC-Transformer")
        if self.cfg.get("use_multiscale_reid"):
            upgrades.append("MultiScale")
        if self.cfg.get("use_gnn_matching") and HAS_TORCH and HAS_NX:
            upgrades.append("GNN")
        print(
            f"[INFO] Camera {camera_id}: UltimateTrackerV2 | Active upgrades: "
            f"{', '.join(upgrades) if upgrades else 'base-only'}"
        )

        self.detector = detector if detector is not None else load_detector_model(self.cfg["det_model"], self.device)
        if tracker_backend is not None:
            self.tracker = tracker_backend
        else:
            from boxmot.trackers import StrongSort

            self.tracker = StrongSort(
                reid_weights=Path(self.cfg["reid_model"]),
                device=self.device,
                half=self.cfg["fp16"],
                min_conf=self.cfg["det_conf"],
                max_cos_dist=0.45,
                max_iou_dist=0.7,
                n_init=self.cfg["min_hits"],
                nn_budget=100,
                mc_lambda=0.98,
                ema_alpha=self.cfg["ema_alpha"],
                max_age=self.cfg["max_age"],
            )

        if feature_extractor is not None:
            self.feature_extractor = feature_extractor
        else:
            base_extractor = RobustFeatureExtractor(self.cfg["reid_model"], self.device, self.cfg["fp16"], self.cfg)
            self.feature_extractor = MultiScalePyramidExtractor(base_extractor, self.cfg)

        self.registry = registry
        self.birth_system = BirthCertificateSystem(self.cfg)
        self.trails: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.cfg["trail_length"]))
        self.feature_cache: "OrderedDict[Tuple, Tuple]" = OrderedDict()
        self.cache_ttl = 5
        self.frame_idx = 0
        self.last_detection_frame = 0
        self._last_detections = np.empty((0, 6), dtype=np.float32)
        self.id_switches = 0
        self.recoveries = 0
        self.births = 0
        self._last_results: List[Tuple] = []

    def _cache_get(self, key: Tuple):
        item = self.feature_cache.get(key)
        if item is None:
            return None
        if self.frame_idx - item[2] >= self.cache_ttl:
            self.feature_cache.pop(key, None)
            return None
        self.feature_cache.move_to_end(key)
        return item

    def _cache_set(self, key: Tuple, value: Tuple):
        self.feature_cache[key] = value
        self.feature_cache.move_to_end(key)
        while len(self.feature_cache) > self.cfg.get("feature_cache_max_size", 256):
            self.feature_cache.popitem(last=False)

    def detect(self, frame: np.ndarray) -> np.ndarray:
        results = self.detector(
            frame,
            conf=self.cfg["det_conf"],
            iou=self.cfg["det_iou"],
            classes=self.cfg["det_classes"],
            imgsz=self.cfg["det_imgsz"],
            device=self.device,
            verbose=False,
        )
        dets = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                dets.append([x1, y1, x2, y2, conf, cls])
        return np.array(dets, dtype=np.float32) if dets else np.empty((0, 6), dtype=np.float32)

    def process_detections(self, frame: np.ndarray, detections: np.ndarray) -> List[Tuple]:
        self.frame_idx += 1
        if self.cfg.get("enable_frame_skip") and self.frame_idx - self.last_detection_frame < self.cfg["frame_skip_interval"]:
            return self._last_results
        self._last_detections = detections
        self.last_detection_frame = self.frame_idx
        tracks = self.tracker.update(detections, frame)
        results = []
        observations = []
        current_tracks = set()
        track_records = []
        if tracks is not None and len(tracks) > 0:
            for t in tracks:
                x1, y1, x2, y2 = int(t[0]), int(t[1]), int(t[2]), int(t[3])
                track_id = int(t[4])
                conf = float(t[5])
                cls = int(t[6])
                bbox = (x1, y1, x2, y2)
                track_key = (self.camera_id, track_id)
                current_tracks.add(track_key)
                bbox_key = (x1 // 10, y1 // 10, (x2 - x1) // 10, (y2 - y1) // 10)
                cached = self._cache_get(bbox_key)
                if cached is not None:
                    deep_feat, color_feat, _ = cached
                    observations.append(
                        {
                            "track_key": track_key,
                            "track_id": track_id,
                            "bbox": bbox,
                            "conf": conf,
                            "cls": cls,
                            "embedding": deep_feat,
                            "color": color_feat,
                        }
                    )
                else:
                    track_records.append((track_key, track_id, bbox, conf, cls, bbox_key))
        if track_records:
            boxes = [rec[2] for rec in track_records]
            confs = [rec[3] for rec in track_records]
            feats = self.feature_extractor.extract_batch(frame, boxes, confs)
            for rec, feat_pair in zip(track_records, feats):
                track_key, track_id, bbox, conf, cls, bbox_key = rec
                deep_feat, color_feat = feat_pair
                self._cache_set(bbox_key, (deep_feat, color_feat, self.frame_idx))
                observations.append(
                    {
                        "track_key": track_key,
                        "track_id": track_id,
                        "bbox": bbox,
                        "conf": conf,
                        "cls": cls,
                        "embedding": deep_feat,
                        "color": color_feat,
                    }
                )
        assignments = {}
        unmatched = observations
        if self.registry and observations:
            assignments, unmatched = self.registry.match_frame(observations, self.camera_id, self.frame_idx)
            for obs in observations:
                track_key = obs["track_key"]
                bbox = obs["bbox"]
                if track_key in assignments:
                    gid = assignments[track_key]["global_id"]
                    stage = assignments[track_key]["stage"]
                    self.registry.assign_track(gid, obs["track_id"], self.camera_id)
                    self.registry.update_identity(gid, obs["embedding"], obs["color"], bbox, self.frame_idx, self.camera_id)
                    if self.cfg.get("use_transformer_motion", True):
                        identity = self.registry.get_identity(gid)
                        if identity:
                            self.registry.transformer_motion.update(gid, bbox, obs["embedding"], identity.velocity)
                    self.trails[gid].append(bbox_center(bbox))
                    results.append((bbox[0], bbox[1], bbox[2], bbox[3], gid, obs["conf"], obs["cls"], stage))
                    self.birth_system.remove(track_key)
                    continue
                cand = self.birth_system.add_observation(track_key, obs["embedding"], obs["color"], bbox, obs["conf"], self.frame_idx)
                if cand is None:
                    continue
                mean_emb = cand.get_mean_embedding()
                mean_col = cand.get_mean_color()
                if self.registry and obs.get("embedding") is not None:
                    persisted_ranked = self.registry.rank_identities({"bbox": bbox, "embedding": mean_emb, "color": mean_col}, self.frame_idx, camera_id=self.camera_id)
                    if persisted_ranked:
                        best_persisted = persisted_ranked[0]
                        is_cross = self.registry.identities[best_persisted["gid"]].last_camera != self.camera_id
                        prefer_thr = self.cfg["direct_match_thr_cross"] if is_cross else self.cfg["direct_match_thr_same"]
                        strong_new_birth = (
                            cand.is_valid(self.cfg, self.frame_idx)
                            and len(cand.embeddings) >= max(2, self.cfg["birth_min_frames"])
                            and float(np.mean(cand.confs)) >= min(0.60, self.cfg["birth_min_conf"] + 0.15)
                        )
                        if best_persisted["score"] >= prefer_thr:
                            continue
                        if not strong_new_birth and best_persisted["score"] > 0.15:
                            continue
                ranked = self.registry.rank_identities({"bbox": bbox, "embedding": mean_emb, "color": mean_col}, self.frame_idx, camera_id=self.camera_id)
                if ranked:
                    best = ranked[0]
                    second = ranked[1]["score"] if len(ranked) > 1 else -1.0
                    if best["score"] >= self.cfg["dup_birth_block_thr"]:
                        continue
                    if best["score"] - second < self.cfg["dup_birth_margin"]:
                        continue
                new_gid = self.registry.create_identity(mean_emb, mean_col, bbox, self.frame_idx, self.camera_id)
                new_gid = self.registry.maybe_merge_recent_birth(new_gid)
                self.registry.assign_track(new_gid, obs["track_id"], self.camera_id)
                self.registry.update_identity(new_gid, obs["embedding"], obs["color"], bbox, self.frame_idx, self.camera_id)
                self.trails[new_gid].append(bbox_center(bbox))
                results.append((bbox[0], bbox[1], bbox[2], bbox[3], new_gid, obs["conf"], obs["cls"], "NEW-BORN"))
                self.births += 1
                self.birth_system.remove(track_key)
        if self.registry:
            lost_tracks = set(self.registry.track_to_global.keys()) - current_tracks
            for cam_id, tid in lost_tracks:
                if cam_id == self.camera_id:
                    self.registry.release_track(tid, self.frame_idx, cam_id)
        self.birth_system.cleanup(current_tracks, self.frame_idx)
        self._last_results = results
        return results

    def track_frame(self, frame: np.ndarray) -> List[Tuple]:
        detections = self.detect(frame)
        return self.process_detections(frame, detections)

