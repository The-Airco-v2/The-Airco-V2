"""Regression tests for local live-pipeline helper behavior."""

from pathlib import Path
from types import SimpleNamespace
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "savant-pipeline"))

from analytics_consumer.main import _parse_optional_uuid
from analytics_consumer.main import _current_cameras_from_bindings, _merge_track_binding, _remove_track_binding
from event_utils import is_full_frame_detection, normalize_track_id
from frame_artifacts import pop_track_artifacts, store_track_artifacts


def test_parse_optional_uuid_tolerates_numeric_source_ids():
    assert _parse_optional_uuid(1) is None
    assert _parse_optional_uuid("1") is None


def test_parse_optional_uuid_accepts_valid_uuid_string():
    value = str(uuid.uuid4())

    assert _parse_optional_uuid(value) == uuid.UUID(value)


def test_is_full_frame_detection_rejects_frame_covering_bbox():
    frame_meta = SimpleNamespace(source_frame_width=1920, source_frame_height=1080)

    assert is_full_frame_detection([0.0, 0.0, 1920.0, 1080.0], frame_meta) is True


def test_is_full_frame_detection_keeps_real_person_bbox():
    frame_meta = SimpleNamespace(source_frame_width=1920, source_frame_height=1080)

    assert is_full_frame_detection([640.0, 120.0, 980.0, 900.0], frame_meta) is False


def test_normalize_track_id_rejects_wrapped_uint64_sentinels():
    assert normalize_track_id(-1) is None
    assert normalize_track_id(18446744073709551615) is None
    assert normalize_track_id("13143147177607954436") is None


def test_normalize_track_id_keeps_signed_values():
    assert normalize_track_id(0) == 0
    assert normalize_track_id(42) == 42
    assert normalize_track_id("9223372036854775807") == 9223372036854775807


def test_frame_artifacts_round_trip():
    store_track_artifacts(
        source_id="1",
        frame_num=42,
        track_id=7,
        crops=[{"type": "body_crop", "b64": "abc", "bbox": [1, 2, 3, 4], "quality": 0.9}],
        snapshot={"full_b64": "xyz", "bbox": [5, 6, 7, 8], "quality": 0.8},
    )

    payload = pop_track_artifacts(source_id="1", frame_num=42, track_id=7)

    assert payload["crops"][0]["type"] == "body_crop"
    assert payload["snapshot"]["full_b64"] == "xyz"
    assert pop_track_artifacts(source_id="1", frame_num=42, track_id=7) == {}


def test_track_binding_helpers_keep_current_cameras_in_sync():
    camera_a = uuid.uuid4()
    camera_b = uuid.uuid4()

    bindings = _merge_track_binding([], camera_a, 11)
    bindings = _merge_track_binding(bindings, camera_a, 11)
    bindings = _merge_track_binding(bindings, camera_b, 12)

    assert bindings == [
        {"camera_id": str(camera_a), "track_id": 11},
        {"camera_id": str(camera_b), "track_id": 12},
    ]
    assert _current_cameras_from_bindings(bindings) == sorted([str(camera_a), str(camera_b)])

    bindings = _remove_track_binding(bindings, camera_a, 11)
    assert bindings == [{"camera_id": str(camera_b), "track_id": 12}]
