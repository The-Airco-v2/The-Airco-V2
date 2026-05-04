"""Test alert generation with deduplication."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from analytics_consumer.alerts import AlertGenerator


@pytest.fixture
def gen():
    return AlertGenerator(phone_threshold_seconds=30, idle_threshold_seconds=300)


def test_unknown_person_alert(gen):
    alert = gen.check_unknown_person(
        session_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
    )
    assert alert is not None
    assert alert["alert_type"] == "unknown_person"


def test_unknown_person_dedup(gen):
    session_id = uuid.uuid4()
    person_id = uuid.uuid4()
    cam = uuid.uuid4()
    t = datetime.now(timezone.utc)

    a1 = gen.check_unknown_person(session_id, person_id, cam, t)
    a2 = gen.check_unknown_person(session_id, person_id, cam, t + timedelta(seconds=10))
    assert a1 is not None
    assert a2 is None


def test_phone_violation_over_threshold(gen):
    alert = gen.check_phone_violation(
        session_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        duration_seconds=45,
        timestamp=datetime.now(timezone.utc),
    )
    assert alert is not None
    assert alert["alert_type"] == "phone_violation"


def test_phone_violation_under_threshold(gen):
    alert = gen.check_phone_violation(
        session_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        duration_seconds=10,
        timestamp=datetime.now(timezone.utc),
    )
    assert alert is None
