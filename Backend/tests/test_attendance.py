"""Test attendance materialization from identity events."""

from datetime import datetime, timezone
import uuid

import pytest

from analytics_consumer.attendance import AttendanceTracker


@pytest.fixture
def tracker():
    entrance_cameras = {uuid.UUID("00000000-0000-0000-0000-000000000001")}
    return AttendanceTracker(entrance_cameras)


def test_check_in_on_entrance_camera(tracker):
    person_id = uuid.uuid4()
    emp_id = uuid.uuid4()
    cam_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    t = datetime(2026, 3, 21, 9, 0, 0, tzinfo=timezone.utc)

    event = tracker.process_identity_event(
        person_id=person_id,
        employee_id=emp_id,
        state="identified",
        camera_id=cam_id,
        timestamp=t,
    )
    assert event is not None
    assert event["event_type"] == "check_in"
    assert event["employee_id"] == emp_id


def test_no_check_in_on_non_entrance_camera(tracker):
    person_id = uuid.uuid4()
    emp_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    t = datetime(2026, 3, 21, 9, 0, 0, tzinfo=timezone.utc)

    event = tracker.process_identity_event(
        person_id=person_id,
        employee_id=emp_id,
        state="identified",
        camera_id=cam_id,
        timestamp=t,
    )
    assert event is None


def test_no_duplicate_check_in(tracker):
    person_id = uuid.uuid4()
    emp_id = uuid.uuid4()
    cam_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    t = datetime(2026, 3, 21, 9, 0, 0, tzinfo=timezone.utc)

    event1 = tracker.process_identity_event(person_id, emp_id, "identified", cam_id, t)
    event2 = tracker.process_identity_event(person_id, emp_id, "identified", cam_id, t)
    assert event1 is not None
    assert event2 is None


def test_unknown_person_no_attendance(tracker):
    person_id = uuid.uuid4()
    cam_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    t = datetime(2026, 3, 21, 9, 0, 0, tzinfo=timezone.utc)

    event = tracker.process_identity_event(person_id, None, "unknown", cam_id, t)
    assert event is None
