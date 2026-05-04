"""Test dwell segment calculation from track events."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from analytics_consumer.dwell import DwellTracker


@pytest.fixture
def tracker():
    return DwellTracker()


def test_simple_dwell_segment(tracker):
    person_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    t0 = datetime(2026, 3, 21, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=5)

    tracker.track_entered(person_id, cam_id, t0)
    segment = tracker.track_exited(person_id, cam_id, t1)

    assert segment is not None
    assert segment["dwell_seconds"] == 300.0
    assert segment["camera_id"] == cam_id


def test_multiple_cameras(tracker):
    person_id = uuid.uuid4()
    cam_a = uuid.uuid4()
    cam_b = uuid.uuid4()
    t0 = datetime(2026, 3, 21, 10, 0, 0, tzinfo=timezone.utc)

    tracker.track_entered(person_id, cam_a, t0)
    tracker.track_entered(person_id, cam_b, t0 + timedelta(seconds=30))

    seg_a = tracker.track_exited(person_id, cam_a, t0 + timedelta(minutes=2))
    seg_b = tracker.track_exited(person_id, cam_b, t0 + timedelta(minutes=5))

    assert seg_a["dwell_seconds"] == 120.0
    assert seg_b["dwell_seconds"] == 270.0


def test_get_dwell_summary(tracker):
    person_id = uuid.uuid4()
    cam_a = uuid.uuid4()
    cam_b = uuid.uuid4()
    t0 = datetime(2026, 3, 21, 10, 0, 0, tzinfo=timezone.utc)

    tracker.track_entered(person_id, cam_a, t0)
    tracker.track_exited(person_id, cam_a, t0 + timedelta(minutes=10))
    tracker.track_entered(person_id, cam_b, t0 + timedelta(minutes=11))
    tracker.track_exited(person_id, cam_b, t0 + timedelta(minutes=15))

    summary = tracker.get_dwell_summary(person_id)
    assert summary[str(cam_a)] == 600.0
    assert summary[str(cam_b)] == 240.0
