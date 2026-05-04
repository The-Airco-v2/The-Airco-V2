"""Dwell segment tracking and materialization."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime


class DwellTracker:
    def __init__(self):
        self._active: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
        self._completed: dict[uuid.UUID, list[dict]] = defaultdict(list)

    def track_entered(self, person_id: uuid.UUID, camera_id: uuid.UUID, timestamp: datetime):
        key = (person_id, camera_id)
        self._active[key] = timestamp

    def track_exited(
        self, person_id: uuid.UUID, camera_id: uuid.UUID, timestamp: datetime
    ) -> dict | None:
        key = (person_id, camera_id)
        entered_at = self._active.pop(key, None)
        if entered_at is None:
            return None

        dwell = (timestamp - entered_at).total_seconds()
        segment = {
            "person_id": person_id,
            "camera_id": camera_id,
            "entered_at": entered_at,
            "exited_at": timestamp,
            "dwell_seconds": dwell,
        }
        self._completed[person_id].append(segment)
        return segment

    def get_dwell_summary(self, person_id: uuid.UUID) -> dict[str, float]:
        summary: dict[str, float] = defaultdict(float)
        for seg in self._completed.get(person_id, []):
            summary[str(seg["camera_id"])] += seg["dwell_seconds"]
        return dict(summary)

    def get_total_dwell(self, person_id: uuid.UUID) -> float:
        return sum(self.get_dwell_summary(person_id).values())
