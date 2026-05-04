from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .utils import l2_normalize


@dataclass
class BirthCandidate:
    track_key: Tuple[str, int]
    first_frame: int
    embeddings: List[np.ndarray]
    colors: List[np.ndarray]
    bboxes: List[Tuple]
    confs: List[float]

    def is_valid(self, cfg: dict, current_frame: int) -> bool:
        if len(self.embeddings) < cfg["birth_min_frames"]:
            return False
        if current_frame - self.first_frame > cfg["birth_max_frames"]:
            return False
        return (sum(self.confs) / len(self.confs)) >= cfg["birth_min_conf"]

    def get_mean_embedding(self) -> np.ndarray:
        if not self.embeddings:
            return np.zeros(512, dtype=np.float32)
        return l2_normalize(np.mean(self.embeddings, axis=0))

    def get_mean_color(self) -> np.ndarray:
        if not self.colors:
            return np.zeros(512, dtype=np.float32)
        return l2_normalize(np.mean(self.colors, axis=0))


class BirthCertificateSystem:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.candidates: Dict[Tuple[str, int], BirthCandidate] = {}

    def add_observation(self, track_key, embedding, color, bbox, conf, frame_idx) -> Optional[BirthCandidate]:
        if track_key not in self.candidates:
            self.candidates[track_key] = BirthCandidate(track_key=track_key, first_frame=frame_idx, embeddings=[], colors=[], bboxes=[], confs=[])
        cand = self.candidates[track_key]
        if embedding is not None:
            cand.embeddings.append(embedding.copy())
        cand.colors.append(color.copy() if color is not None else np.zeros(512, dtype=np.float32))
        cand.bboxes.append(bbox)
        cand.confs.append(conf)
        if len(cand.embeddings) > self.cfg["birth_min_frames"]:
            cand.embeddings.pop(0)
            cand.colors.pop(0)
            cand.bboxes.pop(0)
            cand.confs.pop(0)
        return cand if cand.is_valid(self.cfg, frame_idx) else None

    def remove(self, track_key):
        self.candidates.pop(track_key, None)

    def cleanup(self, active_tracks: set, frame_idx: int):
        to_remove = [
            tid
            for tid, cand in self.candidates.items()
            if tid not in active_tracks or frame_idx - cand.first_frame > self.cfg["birth_max_frames"]
        ]
        for tid in to_remove:
            self.candidates.pop(tid, None)

