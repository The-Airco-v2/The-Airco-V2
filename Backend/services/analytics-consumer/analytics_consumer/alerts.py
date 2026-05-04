"""Alert generation with deduplication."""

from __future__ import annotations

import uuid
from datetime import datetime


class AlertGenerator:
    def __init__(self, phone_threshold_seconds: float = 30, idle_threshold_seconds: float = 300):
        self.phone_threshold = phone_threshold_seconds
        self.idle_threshold = idle_threshold_seconds
        self._seen_dedup_keys: set[str] = set()

    def _dedup(self, key: str) -> bool:
        if key in self._seen_dedup_keys:
            return False
        self._seen_dedup_keys.add(key)
        return True

    def check_unknown_person(
        self, session_id: uuid.UUID, person_id: uuid.UUID, camera_id: uuid.UUID, timestamp: datetime
    ) -> dict | None:
        key = f"unknown:{session_id}:{person_id}"
        if not self._dedup(key):
            return None
        return {
            "alert_type": "unknown_person",
            "severity": "medium",
            "session_id": session_id,
            "session_person_id": person_id,
            "camera_id": camera_id,
            "message": "Unknown person detected on camera",
            "dedup_key": key,
            "timestamp": timestamp,
        }

    def check_phone_violation(
        self,
        session_id: uuid.UUID,
        person_id: uuid.UUID,
        camera_id: uuid.UUID,
        duration_seconds: float,
        timestamp: datetime,
    ) -> dict | None:
        if duration_seconds < self.phone_threshold:
            return None
        key = f"phone:{session_id}:{person_id}"
        if not self._dedup(key):
            return None
        return {
            "alert_type": "phone_violation",
            "severity": "high",
            "session_id": session_id,
            "session_person_id": person_id,
            "camera_id": camera_id,
            "message": f"Phone usage for {duration_seconds:.0f}s (threshold: {self.phone_threshold}s)",
            "dedup_key": key,
            "timestamp": timestamp,
        }

    def check_idle_alert(
        self,
        session_id: uuid.UUID,
        person_id: uuid.UUID,
        camera_id: uuid.UUID,
        idle_seconds: float,
        timestamp: datetime,
    ) -> dict | None:
        if idle_seconds < self.idle_threshold:
            return None
        key = f"idle:{session_id}:{person_id}"
        if not self._dedup(key):
            return None
        return {
            "alert_type": "idle_alert",
            "severity": "low",
            "session_id": session_id,
            "session_person_id": person_id,
            "camera_id": camera_id,
            "message": f"Idle for {idle_seconds:.0f}s (threshold: {self.idle_threshold}s)",
            "dedup_key": key,
            "timestamp": timestamp,
        }
