"""EMA tracklet embedding aggregation.

Formula: E^(k+1) = normalize( (1 - alpha*q) * E^(k) + (alpha*q) * e )
Where alpha=0.3, q=quality (0-1), e=new L2-normalized embedding.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np


@dataclass
class TrackletTemplate:
    embedding: np.ndarray  # L2-normalized, 512-d
    update_count: int = 0
    best_quality: float = 0.0
    best_embedding: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm < 1e-10:
        return v
    return v / norm


class TrackletAggregator:
    ALPHA = 0.3

    def __init__(self) -> None:
        self._templates: dict[tuple[uuid.UUID, str], TrackletTemplate] = {}

    def update(
        self, person_id: uuid.UUID, modality: str, embedding: np.ndarray, quality: float
    ) -> TrackletTemplate:
        """Update EMA template with new observation. Returns updated template."""
        e = _l2_normalize(embedding)
        key = (person_id, modality)
        tpl = self._templates.get(key)

        if tpl is None:
            tpl = TrackletTemplate(
                embedding=e,
                update_count=1,
                best_quality=quality,
                best_embedding=e.copy(),
            )
            self._templates[key] = tpl
            return tpl

        # EMA blend
        w = self.ALPHA * quality
        blended = (1 - w) * tpl.embedding + w * e
        tpl.embedding = _l2_normalize(blended)
        tpl.update_count += 1

        if quality > tpl.best_quality:
            tpl.best_quality = quality
            tpl.best_embedding = e.copy()

        return tpl

    def get_template(self, person_id: uuid.UUID, modality: str) -> TrackletTemplate | None:
        """Return template or None if no observations yet."""
        return self._templates.get((person_id, modality))
