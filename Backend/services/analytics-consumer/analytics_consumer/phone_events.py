"""Phone event accumulation per person."""

from __future__ import annotations

import uuid
from collections import defaultdict


class PhoneTracker:
    def __init__(self):
        self._totals: dict[uuid.UUID, float] = defaultdict(float)

    def add_phone_event(self, person_id: uuid.UUID, duration_seconds: float) -> float:
        self._totals[person_id] += duration_seconds
        return self._totals[person_id]

    def get_total(self, person_id: uuid.UUID) -> float:
        return self._totals.get(person_id, 0.0)

    def get_violation_status(self, person_id: uuid.UUID, threshold: float = 30.0) -> bool:
        return self._totals.get(person_id, 0.0) > threshold
