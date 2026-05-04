from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import sys
import uuid

import numpy as np
import pytest

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from airco.events import StreamNames, TrackEventPayload  # noqa: E402
from airco.models import SessionPerson, SessionPersonTrackBinding  # noqa: E402
from ultimate_adapter.id_bridge import (  # noqa: E402
    CanonicalTrackClosure,
    UltimateCanonicalIdBridge,
)
from ultimate_adapter.output_adapter import UltimateOutputAdapter  # noqa: E402
from ultimate_adapter.ultimate_core import (  # noqa: E402
    IdentitySnapshot,
    TrackSnapshot,
    UltimateCleanupResult,
    UltimateFrameResult,
    UltimateTrackingUpdate,
)


class _FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}

    async def hget(self, key: str, field: str):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str):
        self.hashes.setdefault(key, {})[field] = value


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self):
        self.added: list[object] = []
        self.persons: dict[uuid.UUID, SessionPerson] = {}
        self.bindings: list[SessionPersonTrackBinding] = []
        self.flush = AsyncMock()

    def add(self, model):
        self.added.append(model)
        if isinstance(model, SessionPerson):
            self.persons[model.id] = model
        elif isinstance(model, SessionPersonTrackBinding):
            self.bindings.append(model)

    async def get(self, model, primary_key):
        if model is SessionPerson:
            return self.persons.get(primary_key)
        return None

    async def execute(self, statement):
        try:
            values = list(statement.compile().params.values())
        except Exception:  # pragma: no cover - defensive only
            values = []
        session_id, camera_id, track_id, binding_state = values
        for binding in self.bindings:
            if (
                binding.session_id == session_id
                and binding.camera_id == camera_id
                and binding.track_id == track_id
                and binding.binding_state == binding_state
            ):
                return _ScalarResult(binding)
        return _ScalarResult(None)


def _make_update(
    *,
    session_id: uuid.UUID,
    camera_id: uuid.UUID,
    global_id: int,
    track_id: int,
    frame_index: int = 1,
) -> UltimateTrackingUpdate:
    return UltimateTrackingUpdate(
        session_id=str(session_id),
        camera_id=str(camera_id),
        frame_index=frame_index,
        track=TrackSnapshot(
            track_id=track_id,
            track_key={"session_id": str(session_id), "camera_id": str(camera_id), "track_id": track_id},
            bbox={"x1": 10, "y1": 20, "x2": 30, "y2": 40},
            confidence=0.91,
            class_id=0,
        ),
        identity=IdentitySnapshot(
            global_id=global_id,
            stage="TRACKED",
            lifecycle="tracked",
            active_track_id=track_id,
            birth_camera=str(camera_id),
            last_camera=str(camera_id),
        ),
    )


@pytest.mark.asyncio
async def test_id_bridge_creates_canonical_session_person_and_binding_for_new_global_id():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    db = _FakeDb()
    bridge = UltimateCanonicalIdBridge(_FakeRedis())
    observed_at = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)

    resolution = await bridge.record_track_observation(
        db,
        update=_make_update(
            session_id=session_id,
            camera_id=camera_id,
            global_id=101,
            track_id=11,
        ),
        observed_at=observed_at,
    )

    assert resolution is not None
    assert resolution.person_created is True
    assert resolution.track_event_type == "track_started"
    person = db.persons[resolution.session_person_id]
    assert person.display_name.startswith("Unknown Person ")
    assert person.current_cameras == [str(camera_id)]
    assert person.evidence_summary["ultimate_global_id"] == 101
    assert len(db.bindings) == 1
    assert db.bindings[0].session_person_id == resolution.session_person_id
    assert db.bindings[0].binding_state == "active"


@pytest.mark.asyncio
async def test_id_bridge_reuses_existing_mapping_and_updates_binding():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    observed_at = datetime(2026, 4, 19, 12, 5, tzinfo=timezone.utc)
    redis = _FakeRedis()
    db = _FakeDb()

    person = SessionPerson(
        id=person_id,
        session_id=session_id,
        tenant_id="default",
        recognition_state="unknown",
        display_name="Unknown",
        current_cameras=[str(camera_id)],
        active_track_bindings=[{"camera_id": str(camera_id), "track_id": 11}],
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        is_active=True,
        evidence_summary={"source": "ultimate", "ultimate_global_id": 101},
    )
    binding = SessionPersonTrackBinding(
        id=uuid.uuid4(),
        session_id=session_id,
        session_person_id=person_id,
        camera_id=camera_id,
        track_id=11,
        binding_state="active",
        started_at=observed_at,
        last_seen_at=observed_at,
        source="ultimate_track",
        confidence=0.5,
        evidence_summary={},
    )
    db.persons[person_id] = person
    db.bindings.append(binding)
    await redis.hset(f"airco:ultimate-adapter:session-person-map:{session_id}", "101", str(person_id))

    bridge = UltimateCanonicalIdBridge(redis)
    resolution = await bridge.record_track_observation(
        db,
        update=_make_update(
            session_id=session_id,
            camera_id=camera_id,
            global_id=101,
            track_id=11,
            frame_index=2,
        ),
        observed_at=observed_at.replace(minute=6),
    )

    assert resolution is not None
    assert resolution.person_created is False
    assert resolution.track_event_type == "track_observed"
    assert len(db.bindings) == 1
    assert binding.last_seen_at == observed_at.replace(minute=6)
    assert binding.evidence_summary["ultimate_global_id"] == 101


@dataclass
class _BridgeResult:
    session_person_id: uuid.UUID
    track_event_type: str
    person_created: bool
    recognition_state: str = "unknown"


class _FakeBridge:
    def __init__(self, person_id: uuid.UUID):
        self.person_id = person_id
        self.observations: list[int] = []
        self.closed: list[int] = []

    async def record_track_observation(self, db, *, update, observed_at):
        self.observations.append(update.track.track_id)
        return _BridgeResult(
            session_person_id=self.person_id,
            track_event_type="track_started" if len(self.observations) == 1 else "track_observed",
            person_created=len(self.observations) == 1,
        )

    async def close_track(self, db, *, session_id, camera_id, track_id, observed_at):
        self.closed.append(track_id)
        return CanonicalTrackClosure(session_person_id=self.person_id, closed=True)


@pytest.mark.asyncio
async def test_output_adapter_publishes_track_and_identity_events_for_new_person():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    published: list[tuple[str, dict]] = []

    async def fake_publish(stream: str, event: dict):
        published.append((stream, event))

    adapter = UltimateOutputAdapter(
        redis_client=_FakeRedis(),
        bridge=_FakeBridge(person_id),
        publisher=fake_publish,
    )
    result = UltimateFrameResult(
        session_id=str(session_id),
        camera_id=str(camera_id),
        topology={},
        frame_index=1,
        updates=[_make_update(session_id=session_id, camera_id=camera_id, global_id=1, track_id=22)],
        births=1,
        active_identities=1,
        total_identities=1,
    )

    await adapter.publish_frame_result(_FakeDb(), result=result)

    assert [stream for stream, _ in published] == [StreamNames.TRACKS, StreamNames.IDENTITY]
    track_event = TrackEventPayload.from_redis(published[0][1])
    assert track_event.event_type == "track_started"
    assert track_event.track_id == 22
    assert published[1][1]["event_type"] == "person_created"
    assert published[1][1]["session_person_id"] == str(person_id)


@pytest.mark.asyncio
async def test_output_adapter_publishes_body_and_face_crops_and_snapshot_when_frame_present():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    published: list[tuple[str, dict]] = []

    async def fake_publish(stream: str, event: dict):
        published.append((stream, event))

    adapter = UltimateOutputAdapter(
        redis_client=_FakeRedis(),
        bridge=_FakeBridge(person_id),
        publisher=fake_publish,
        snapshot_interval_frames=30,
    )
    result = UltimateFrameResult(
        session_id=str(session_id),
        camera_id=str(camera_id),
        topology={},
        frame_index=1,
        updates=[_make_update(session_id=session_id, camera_id=camera_id, global_id=1, track_id=22)],
        births=1,
        active_identities=1,
        total_identities=1,
    )
    frame = np.full((64, 64, 3), 255, dtype=np.uint8)

    await adapter.publish_frame_result(_FakeDb(), result=result, frame=frame)

    streams = [stream for stream, _ in published]
    assert streams.count(StreamNames.CROPS) == 2
    assert StreamNames.SNAPSHOTS in streams
    crop_events = [event for stream, event in published if stream == StreamNames.CROPS]
    snapshot_event = next(event for stream, event in published if stream == StreamNames.SNAPSHOTS)
    assert [event["event_type"] for event in crop_events] == ["body_crop", "face_crop"]
    assert snapshot_event["event_type"] == "snapshot_requested"
    assert snapshot_event["session_person_id"] == str(person_id)


@pytest.mark.asyncio
async def test_output_adapter_closes_missing_tracks_on_next_frame():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    published: list[tuple[str, dict]] = []

    async def fake_publish(stream: str, event: dict):
        published.append((stream, event))

    bridge = _FakeBridge(person_id)
    adapter = UltimateOutputAdapter(
        redis_client=_FakeRedis(),
        bridge=bridge,
        publisher=fake_publish,
    )
    db = _FakeDb()

    first = UltimateFrameResult(
        session_id=str(session_id),
        camera_id=str(camera_id),
        topology={},
        frame_index=1,
        updates=[_make_update(session_id=session_id, camera_id=camera_id, global_id=1, track_id=22)],
        births=1,
        active_identities=1,
        total_identities=1,
    )
    second = UltimateFrameResult(
        session_id=str(session_id),
        camera_id=str(camera_id),
        topology={},
        frame_index=2,
        updates=[],
        births=0,
        active_identities=0,
        total_identities=1,
    )

    await adapter.publish_frame_result(db, result=first)
    await adapter.publish_frame_result(db, result=second)

    assert bridge.closed == [22]
    track_events = [TrackEventPayload.from_redis(event) for stream, event in published if stream == StreamNames.TRACKS]
    assert [event.event_type for event in track_events] == ["track_started", "track_ended"]


@pytest.mark.asyncio
async def test_output_adapter_cleanup_closes_all_active_tracks():
    session_id = uuid.uuid4()
    camera_id = uuid.uuid4()
    person_id = uuid.uuid4()
    published: list[tuple[str, dict]] = []

    async def fake_publish(stream: str, event: dict):
        published.append((stream, event))

    bridge = _FakeBridge(person_id)
    adapter = UltimateOutputAdapter(
        redis_client=_FakeRedis(),
        bridge=bridge,
        publisher=fake_publish,
    )
    db = _FakeDb()

    await adapter.publish_frame_result(
        db,
        result=UltimateFrameResult(
            session_id=str(session_id),
            camera_id=str(camera_id),
            topology={},
            frame_index=1,
            updates=[_make_update(session_id=session_id, camera_id=camera_id, global_id=1, track_id=22)],
            births=1,
            active_identities=1,
            total_identities=1,
        ),
    )
    await adapter.publish_cleanup(
        db,
        result=UltimateCleanupResult(
            session_id=str(session_id),
            camera_id=str(camera_id),
            frame_index=2,
            released_tracks=1,
            active_tracks=0,
            total_identities=1,
            closed=True,
        ),
    )

    assert bridge.closed == [22]
    track_events = [TrackEventPayload.from_redis(event) for stream, event in published if stream == StreamNames.TRACKS]
    assert [event.event_type for event in track_events] == ["track_started", "track_ended"]
