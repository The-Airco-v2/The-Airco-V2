"""Gallery maintenance — TTL expiry, compaction, merge proposals."""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
import numpy as np


class GalleryMaintenance:
    DEFAULT_TTL_DAYS = 7
    DEFAULT_MERGE_THRESHOLD = 0.85

    def __init__(self, ttl_days: int = DEFAULT_TTL_DAYS):
        self.ttl_days = ttl_days

    def should_expire(self, person, now: datetime) -> bool:
        """Return True if this unknown person should be expired.
        Only unknowns expire. Identified/corrected/candidate persons kept forever.
        """
        if person.recognition_state in ("identified", "corrected", "candidate"):
            return False
        if person.merged_into_session_person_id is not None:
            return False
        cutoff = now - timedelta(days=self.ttl_days)
        return person.last_seen_at < cutoff

    def find_merge_candidates(
        self,
        gallery: dict[uuid.UUID, np.ndarray],
        threshold: float = DEFAULT_MERGE_THRESHOLD,
    ) -> list[tuple[uuid.UUID, uuid.UUID, float]]:
        """Find pairs of unknown persons with similar templates.
        Returns list of (person_a, person_b, similarity) sorted by sim desc.
        """
        person_ids = list(gallery.keys())
        proposals = []
        for i in range(len(person_ids)):
            emb_a = gallery[person_ids[i]]
            norm_a = np.linalg.norm(emb_a)
            if norm_a == 0:
                continue
            a_normed = emb_a / norm_a
            for j in range(i + 1, len(person_ids)):
                emb_b = gallery[person_ids[j]]
                norm_b = np.linalg.norm(emb_b)
                if norm_b == 0:
                    continue
                b_normed = emb_b / norm_b
                sim = float(np.dot(a_normed, b_normed))
                if sim >= threshold:
                    proposals.append((person_ids[i], person_ids[j], sim))
        proposals.sort(key=lambda x: x[2], reverse=True)
        return proposals
