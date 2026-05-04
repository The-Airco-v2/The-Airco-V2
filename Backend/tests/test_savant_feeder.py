"""Unit tests for the multi-camera Savant feeder helpers."""

from pathlib import Path
import sys

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "services" / "savant-feeder"),
)

from savant_feeder.runtime import (
    parse_active_camera_ids,
    session_camera_stream_name,
    stream_rtsp_url,
)


def test_parse_active_camera_ids_accepts_json_lists():
    assert parse_active_camera_ids('["cam-1", "cam-2"]') == ["cam-1", "cam-2"]


def test_parse_active_camera_ids_rejects_invalid_payloads():
    assert parse_active_camera_ids(None) == []
    assert parse_active_camera_ids("") == []
    assert parse_active_camera_ids("{}") == []
    assert parse_active_camera_ids("not-json") == []


def test_session_camera_stream_name_matches_session_control_contract():
    assert (
        session_camera_stream_name("session-123", "camera-456")
        == "session_session_123_camera_456"
    )


def test_stream_rtsp_url_uses_deterministic_go2rtc_stream_name():
    assert (
        stream_rtsp_url("rtsp://host.docker.internal:8556", "session-123", "camera-456")
        == "rtsp://host.docker.internal:8556/session_session_123_camera_456"
    )
