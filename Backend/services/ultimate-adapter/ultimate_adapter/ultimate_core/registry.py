from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import numpy as np

from .birth import BirthCertificateSystem
from .config import coerce_core_config
from .gallery import PersistentEmbeddingGallery
from .identity import PersonIdentity
from .motion import CrossCameraGraphMatcher, TemporalConsistencyManager, TransformerMotionManager, HAS_SCIPY, linear_sum_assignment
from .utils import bbox_center, bbox_iou, cosine_sim, l2_normalize


class GlobalIdentityRegistry:
    def __init__(self, cfg: dict, gallery: PersistentEmbeddingGallery | None = None):
        self.cfg = coerce_core_config(cfg)
        self.identities: Dict[int, PersonIdentity] = {}
        self.track_to_global: Dict[Tuple[str, int], int] = {}
        self.next_global_id = 1
        self.lock = threading.RLock()
        self.camera_galleries: Dict[str, Dict[int, int]] = defaultdict(dict)
        self.gallery = gallery or PersistentEmbeddingGallery(
            storage_dir=self.cfg.get("embedding_storage_dir") or self._default_embedding_storage_dir(),
            max_viewpoints=self.cfg.get("bank_max_embeds", 25),
        )
        self.transformer_motion = TransformerMotionManager(self.cfg)
        self.temporal_consistency = TemporalConsistencyManager(self.cfg)
        self.graph_matcher = CrossCameraGraphMatcher(self.cfg)
        self._load_persisted_identities()

    def _default_embedding_storage_dir(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent / "body_embedding")

    def close(self):
        if hasattr(self.gallery, "stop"):
            self.gallery.stop()

    def prune_stale_identities(self):
        cutoff = float(self.cfg.get("identity_prune_seconds", 7 * 24 * 3600))
        now = time.time()
        stale = [
            gid
            for gid, identity in self.identities.items()
            if identity.active_track_id is None and (now - identity.last_seen_time) > cutoff
        ]
        for gid in stale:
            identity = self.identities.pop(gid, None)
            if identity is None:
                continue
            if identity.last_track_key in self.track_to_global:
                self.track_to_global.pop(identity.last_track_key, None)
            if identity.active_track_id is not None and identity.last_camera in self.camera_galleries:
                self.camera_galleries[identity.last_camera].pop(identity.active_track_id, None)

    def _merge_recent_births(self, keep_gid: int) -> int:
        keep = self.identities.get(keep_gid)
        if keep is None:
            return keep_gid
        window = int(self.cfg.get("recent_birth_merge_frames", 10))
        thr = float(self.cfg.get("recent_birth_merge_thr", 0.75))
        candidates = [
            gid
            for gid, identity in self.identities.items()
            if gid != keep_gid and abs(identity.birth_frame - keep.birth_frame) <= window
        ]
        best_gid = keep_gid
        best_score = thr
        for gid in candidates:
            identity = self.identities.get(gid)
            if identity is None:
                continue
            score = keep.compute_fused_similarity(identity.ema_embedding, identity.color_signature, self.cfg, query_camera=keep.last_camera)
            if score > best_score:
                best_score = score
                best_gid = gid
        if best_gid == keep_gid:
            return keep_gid
        if keep.birth_frame <= self.identities[best_gid].birth_frame:
            drop_gid = best_gid
        else:
            drop_gid = keep_gid
            keep_gid = best_gid
            keep = self.identities.get(keep_gid)
            if keep is None:
                return best_gid
        drop = self.identities.get(drop_gid)
        if keep is None or drop is None:
            return keep_gid
        keep.merge_from(drop)
        if drop.active_track_id is not None:
            self.track_to_global.pop((drop.last_camera, drop.active_track_id), None)
        self.gallery.delete_identity(drop_gid)
        self.identities.pop(drop_gid, None)
        return keep_gid

    def _identity_pair_threshold(self, identity: PersonIdentity, obs_camera: str) -> float:
        if identity.last_camera == obs_camera:
            return self.cfg["direct_match_thr_same"]
        return self.graph_matcher.get_cross_camera_threshold(identity.last_camera, obs_camera, self.cfg["direct_match_thr_cross"])

    def _load_persisted_identities(self):
        persisted = self.gallery.load_all_identities()
        for gid, data in persisted.items():
            identity = PersonIdentity(
                global_id=gid,
                seed_embedding=data.get("seed_embedding") if data.get("seed_embedding") is not None else np.zeros(512, dtype=np.float32),
                ema_embedding=data.get("ema_embedding") if data.get("ema_embedding") is not None else np.zeros(512, dtype=np.float32),
                viewpoint_bank=data.get("viewpoint_bank", deque(maxlen=self.cfg["bank_max_embeds"])),
                color_signature=data.get("color_signature"),
                birth_frame=int(data.get("birth_frame", -1)),
                last_seen_frame=int(data.get("last_seen_frame", -1)),
                last_bbox=tuple(data.get("last_bbox", (0, 0, 0, 0))),
                last_center=tuple(data.get("last_center", (0.0, 0.0))),
                center_history=deque([tuple(data.get("last_center", (0.0, 0.0)))], maxlen=12),
                velocity=tuple(data.get("velocity", (0.0, 0.0))),
                birth_camera=str(data.get("birth_camera", "1")),
                last_camera=str(data.get("last_camera", data.get("birth_camera", "1"))),
                last_seen_time=float(data.get("last_seen_time", time.time())),
                total_detections=int(data.get("total_detections", 0)),
                lock_until_frame=int(data.get("lock_until_frame", -1)),
                last_match_score=float(data.get("last_match_score", 0.0)),
            )
            if identity.last_bbox != (0, 0, 0, 0):
                identity.center_history.append(identity.last_center)
            if not identity.ukf_initialized and identity.last_center != (0.0, 0.0):
                identity.init_ukf(identity.last_center, self.cfg)
            self.identities[gid] = identity
        if self.identities:
            self.next_global_id = max(self.identities.keys()) + 1

    def create_identity(self, seed_embedding: np.ndarray, color_sig: np.ndarray, bbox: Tuple, frame_idx: int, camera_id: str) -> int:
        with self.lock:
            gid = self.next_global_id
            self.next_global_id += 1
            center = bbox_center(bbox)
            identity = PersonIdentity(
                global_id=gid,
                seed_embedding=l2_normalize(seed_embedding.copy()),
                ema_embedding=l2_normalize(seed_embedding.copy()),
                viewpoint_bank=deque([l2_normalize(seed_embedding.copy())], maxlen=self.cfg["bank_max_embeds"]),
                color_signature=color_sig.copy() if color_sig is not None else None,
                birth_frame=frame_idx,
                last_seen_frame=frame_idx,
                last_bbox=bbox,
                last_center=center,
                center_history=deque([center], maxlen=12),
                total_detections=1,
                lock_until_frame=frame_idx + 6,
                birth_camera=camera_id,
                last_camera=camera_id,
            )
            identity.init_ukf(center, self.cfg)
            self.identities[gid] = identity
            self.gallery.save_identity(identity)
            return self._merge_recent_births(gid)

    def _score_identity(self, identity: PersonIdentity, obs: dict, frame_idx: int, obs_camera: str = "1") -> Tuple[float, float, float, float, float, int]:
        gap = max(0, frame_idx - identity.last_seen_frame)
        elapsed_seconds = max(0.0, time.time() - identity.last_seen_time)
        embedding = obs.get("embedding")
        color = obs.get("color")
        bbox = obs["bbox"]
        # Keep camera handoff sequential: an identity still owned by another live camera
        # should not be reused cross-camera until that source track is released.
        if identity.active_track_id is not None and identity.last_camera != obs_camera:
            return -1.0, 0.0, 0.0, 0.0, 0.0, gap
        if identity.last_camera != obs_camera:
            if not self.graph_matcher.is_transition_possible(identity.last_camera, obs_camera, elapsed_seconds):
                return -1.0, 0.0, 0.0, 0.0, 0.0, gap
        appearance = identity.compute_fused_similarity(embedding, color, self.cfg, query_camera=obs_camera)
        predicted_bbox = identity.predict_bbox(max(gap, 1))
        iou = bbox_iou(bbox, predicted_bbox)
        motion = identity.motion_similarity(bbox, max(gap, 1), self.cfg["motion_sigma_px"])
        temporal = float(np.exp(-gap / max(self.cfg["temporal_decay_frames"], 1.0)))
        cross_decay = 1.0
        if identity.last_camera != obs_camera:
            cross_decay = float(np.exp(-elapsed_seconds / max(self.cfg.get("cross_camera_tau_sec", 60.0), 1.0)))
        motion_w = float(self.cfg["motion_weight"] * np.exp(-gap / 30.0))
        iou_w = float(self.cfg["iou_weight"] * np.exp(-gap / 15.0))
        temporal_w = float(self.cfg["temporal_weight"] * np.exp(-gap / max(self.cfg["temporal_decay_frames"], 1.0)))
        appear_w = max(0.0, 1.0 - motion_w - iou_w - temporal_w)
        score = appear_w * appearance + motion_w * motion + iou_w * iou + temporal_w * temporal
        if identity.last_camera != obs_camera:
            elapsed = time.time() - identity.last_seen_time
            score = self.graph_matcher.boost_score(score, identity.last_camera, obs_camera, elapsed)
            if identity.active_track_id is None and gap > 10:
                self.graph_matcher.update_transition(identity.last_camera, obs_camera, elapsed)
            score *= cross_decay
        if identity.active_track_id is not None:
            score -= self.cfg["active_identity_penalty"]
        if identity.lock_until_frame >= frame_idx and appearance < self.cfg["lock_cosine_thr"]:
            score -= 0.20
        return score, appearance, iou, motion, temporal, gap

    def rank_identities(self, obs: dict, frame_idx: int, candidate_ids: Optional[List[int]] = None, camera_id: str = "1"):
        if candidate_ids is None:
            candidate_ids = list(self.identities.keys())
        ranked = []
        for gid in candidate_ids:
            identity = self.identities.get(gid)
            if identity is None:
                continue
            score, appearance, iou, motion, temporal, gap = self._score_identity(identity, obs, frame_idx, camera_id)
            ranked.append({"gid": gid, "score": score, "appearance": appearance, "iou": iou, "motion": motion, "temporal": temporal, "gap": gap})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def match_frame(self, observations: List[dict], camera_id: str, frame_idx: int):
        if not observations:
            return {}, []
        self.prune_stale_identities()
        candidate_ids = []
        for gid, identity in self.identities.items():
            gap = frame_idx - identity.last_seen_frame
            if gap <= self.cfg["occlusion_max_frames"] or identity.active_track_id is not None:
                candidate_ids.append(gid)
        if len(candidate_ids) > self.cfg["max_match_identities"]:
            candidate_ids.sort(key=lambda gid: (self.identities[gid].active_track_id is None, frame_idx - self.identities[gid].last_seen_frame))
            candidate_ids = candidate_ids[: self.cfg["max_match_identities"]]
        if not candidate_ids:
            return {}, observations
        gallery_embs = [self.identities[g].ema_embedding for g in candidate_ids]
        gallery_cams = [self.identities[g].last_camera for g in candidate_ids]
        score_mat = np.full((len(observations), len(candidate_ids)), -1.0, dtype=np.float32)
        for i, obs in enumerate(observations):
            if obs.get("embedding") is not None and self.cfg.get("use_gnn_matching", True):
                gnn_sims = self.graph_matcher.gnn_rerank(obs["embedding"], gallery_embs, gallery_cams, camera_id)
            else:
                gnn_sims = None
            for j, gid in enumerate(candidate_ids):
                identity = self.identities[gid]
                pair_score, appearance, iou, motion, temporal, gap = self._score_identity(identity, obs, frame_idx, camera_id)
                if pair_score < 0:
                    score_mat[i, j] = pair_score
                    continue
                if gnn_sims is not None and j < len(gnn_sims):
                    is_cross = identity.last_camera != camera_id
                    gnn_weight = 0.4 if is_cross else 0.1
                    pair_score = (1.0 - gnn_weight) * pair_score + gnn_weight * float(gnn_sims[j])
                if identity.lock_until_frame >= frame_idx and identity.active_track_id is not None:
                    if appearance < self.cfg["lock_cosine_thr"]:
                        pair_score -= 0.25
                score_mat[i, j] = pair_score
        if HAS_SCIPY:
            cost = 1.0 - np.clip(score_mat, 0.0, 1.0)
            row_ind, col_ind = linear_sum_assignment(cost)
        else:
            row_ind = np.arange(len(observations))
            col_ind = np.argmax(score_mat, axis=1)
        assignments = {}
        used_rows = set()
        for r, c in zip(row_ind, col_ind):
            obs = observations[r]
            gid = candidate_ids[c]
            best_score = float(score_mat[r, c])
            row_scores = np.sort(score_mat[r])[::-1]
            best = float(row_scores[0]) if len(row_scores) > 0 else -1.0
            second = float(row_scores[1]) if len(row_scores) > 1 else -1.0
            margin = best - second if second > -1e9 else 1.0
            if best < self.cfg["hungarian_match_thr"]:
                continue
            identity = self.identities[gid]
            is_cross = identity.last_camera != camera_id
            direct_thr = self._identity_pair_threshold(identity, camera_id)
            bank_thr = self.cfg["bank_cosine_thr_cross"] if is_cross else self.cfg["bank_cosine_thr_same"]
            if margin < self.cfg["ambiguous_margin"] and best < direct_thr:
                continue
            gap = frame_idx - identity.last_seen_frame
            appearance = identity.compute_fused_similarity(obs.get("embedding", identity.ema_embedding), obs.get("color"), self.cfg, query_camera=camera_id)
            iou = bbox_iou(obs["bbox"], identity.predict_bbox(max(gap, 1)))
            if appearance < bank_thr:
                continue
            if iou >= self.cfg["stage1_iou_thr"] and gap <= self.cfg["stage1_max_gap"]:
                stage = "S1"
            elif appearance >= self.cfg["stage2_cos_thr"]:
                stage = "S2"
            elif appearance >= self.cfg["stage3_seed_thr"]:
                stage = "S3"
            else:
                stage = "S4"
            assignments[obs["track_key"]] = {"global_id": gid, "score": best_score, "stage": stage}
            used_rows.add(r)
        unmatched = [obs for idx, obs in enumerate(observations) if idx not in used_rows]
        return assignments, unmatched

    def assign_track(self, global_id: int, track_id: int, camera_id: str):
        with self.lock:
            if global_id not in self.identities:
                return
            track_key = (camera_id, track_id)
            identity = self.identities[global_id]
            prev_key = identity.last_track_key
            if prev_key is not None and prev_key != track_key and prev_key in self.track_to_global:
                del self.track_to_global[prev_key]
            identity.active_track_id = track_id
            identity.last_track_key = track_key
            identity.last_camera = camera_id
            self.track_to_global[track_key] = global_id
            self.camera_galleries[camera_id][track_id] = global_id

    def release_track(self, track_id: int, frame_idx: int, camera_id: str):
        with self.lock:
            track_key = (camera_id, track_id)
            if track_key in self.track_to_global:
                gid = self.track_to_global[track_key]
                if gid in self.identities:
                    identity = self.identities[gid]
                    identity.active_track_id = None
                    identity.last_seen_frame = frame_idx
                    identity.is_occluded = True
                    identity.occlusion_start_frame = frame_idx
                del self.track_to_global[track_key]
                if camera_id in self.camera_galleries:
                    self.camera_galleries[camera_id].pop(track_id, None)
                self.transformer_motion.remove(gid)

    def release_camera_tracks(self, camera_id: str, frame_idx: int):
        with self.lock:
            keys = [key for key in self.track_to_global.keys() if key[0] == camera_id]
            for _, track_id in keys:
                self.release_track(track_id, frame_idx, camera_id)

    def update_identity(self, global_id: int, new_embedding: np.ndarray, new_color: np.ndarray, bbox: Tuple, frame_idx: int, camera_id: str = "1"):
        with self.lock:
            if global_id not in self.identities:
                return
            identity = self.identities[global_id]
            prev_camera = identity.last_camera
            if new_embedding is not None:
                consistent_emb, change_score = self.temporal_consistency.update_embedding(global_id, new_embedding, identity.ema_embedding)
                identity.ema_embedding = consistent_emb
                identity.appearance_change_scores.append(change_score)
            else:
                consistent_emb = identity.ema_embedding
            if self.cfg.get("use_transformer_motion", True):
                self.transformer_motion.update(global_id, bbox, new_embedding, identity.velocity)
            if new_embedding is not None:
                identity.add_viewpoint(new_embedding)
            if new_color is not None:
                if identity.color_signature is None:
                    identity.color_signature = new_color
                else:
                    identity.color_signature = l2_normalize(0.9 * identity.color_signature + 0.1 * new_color)
            identity.update_motion(bbox, frame_idx, self.cfg)
            identity.is_occluded = False
            identity.last_camera = camera_id
            identity.detection_history.append(frame_idx)
            identity.last_match_score = float(identity.compute_fused_similarity(consistent_emb, new_color, self.cfg))
            if identity.total_detections >= self.cfg["lock_after_frames"]:
                identity.lock_until_frame = max(identity.lock_until_frame, frame_idx + 10)
            if (identity.total_detections % 5 == 0) or (prev_camera != camera_id):
                self.gallery.save_identity(identity)

    def maybe_merge_recent_birth(self, global_id: int) -> int:
        with self.lock:
            if global_id not in self.identities:
                return global_id
            return self._merge_recent_births(global_id)

    def get_identity(self, global_id: int) -> Optional[PersonIdentity]:
        return self.identities.get(global_id)

    def get_active_count(self) -> int:
        return sum(1 for identity in self.identities.values() if identity.active_track_id is not None)

    def get_total_count(self) -> int:
        return len(self.identities)
