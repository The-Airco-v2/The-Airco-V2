"""Test that event schemas validate correctly and serialize to/from Redis."""

import uuid
from datetime import datetime, timezone

from airco.events import (
    TrackEventPayload,
    CropEventPayload,
    PhoneEventPayload,
    IdentityEventPayload,
    SnapshotEventPayload,
    AlertEventPayload,
    StreamNames,
)


def test_track_event_roundtrip():
    e = TrackEventPayload(
        event_type="track_started",
        session_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        track_id=42,
        bbox=[100, 200, 300, 400],
        confidence=0.87,
        timestamp=datetime.now(timezone.utc),
        frame_number=12345,
    )
    d = e.to_redis()
    assert d["event_type"] == "track_started"
    assert d["track_id"] == "42"
    back = TrackEventPayload.from_redis(d)
    assert back.track_id == 42
    assert back.confidence == 0.87


def test_crop_event_has_jpeg_bytes():
    e = CropEventPayload(
        event_type="face_crop",
        session_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        track_id=10,
        crop_b64="aGVsbG8=",  # base64 of "hello"
        bbox=[10, 20, 50, 60],
        quality_score=0.9,
        timestamp=datetime.now(timezone.utc),
    )
    d = e.to_redis()
    assert "crop_b64" in d


def test_identity_event_states():
    e = IdentityEventPayload(
        event_type="person_identified",
        session_id=uuid.uuid4(),
        session_person_id=uuid.uuid4(),
        recognition_state="identified",
        track_id=7,
        employee_id=uuid.uuid4(),
        employee_name="Test Employee",
        camera_id=uuid.uuid4(),
        confidence=0.92,
        timestamp=datetime.now(timezone.utc),
    )
    assert e.recognition_state == "identified"
    assert e.to_redis()["track_id"] == "7"


def test_stream_names():
    assert StreamNames.TRACKS == "airco:tracks"
    assert StreamNames.CROPS == "airco:crops"
    assert StreamNames.IDENTITY == "airco:identity"
