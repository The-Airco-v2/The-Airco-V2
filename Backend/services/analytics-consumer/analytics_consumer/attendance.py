"""Attendance materialization from identity events."""

from __future__ import annotations

import uuid
from datetime import datetime


class AttendanceTracker:
    def __init__(self, entrance_cameras: set[uuid.UUID]):
        self.entrance_cameras = entrance_cameras
        self._checked_in: set[uuid.UUID] = set()

    def process_identity_event(
        self,
        person_id: uuid.UUID,
        employee_id: uuid.UUID | None,
        state: str,
        camera_id: uuid.UUID,
        timestamp: datetime,
    ) -> dict | None:
        if state not in ("identified", "corrected"):
            return None
        if employee_id is None:
            return None
        if camera_id not in self.entrance_cameras:
            return None
        if employee_id in self._checked_in:
            return None

        self._checked_in.add(employee_id)

        return {
            "event_type": "check_in",
            "session_person_id": person_id,
            "employee_id": employee_id,
            "camera_id": camera_id,
            "timestamp": timestamp,
            "confidence": 1.0,
        }
