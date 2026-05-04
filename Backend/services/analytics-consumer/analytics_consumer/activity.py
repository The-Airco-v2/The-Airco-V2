"""Activity classification tracking per person."""

from __future__ import annotations

import uuid
from collections import defaultdict


class ActivityTracker:
    def __init__(self):
        self._totals: dict[uuid.UUID, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._current: dict[uuid.UUID, str] = {}

    def update_activity(self, person_id: uuid.UUID, activity: str, duration_seconds: float = 1.0):
        self._totals[person_id][activity] += duration_seconds
        self._current[person_id] = activity

    def get_current(self, person_id: uuid.UUID) -> str:
        return self._current.get(person_id, "unknown")

    def get_summary(self, person_id: uuid.UUID) -> dict:
        totals = self._totals.get(person_id, {})
        working = totals.get("working", 0)
        idle = totals.get("idle", 0)
        walking = totals.get("walking", 0)
        total = working + idle + walking
        return {
            "working_seconds": working,
            "idle_seconds": idle,
            "walking_seconds": walking,
            "productivity_percent": round((working / total * 100) if total > 0 else 0),
        }
