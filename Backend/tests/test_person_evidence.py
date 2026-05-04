"""Helper-level tests for unknown-person evidence curation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.person_evidence import (
    build_storyboard,
    build_unknown_person_timeline,
    dedupe_moments,
    derive_continuity_confidence,
    select_person_evidence_snapshots,
    select_dwell_checkpoints,
    select_structural_moments,
)


def _ts(offset_seconds: int) -> datetime:
    return datetime(2026, 4, 4, 9, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def _moment(kind: str, offset_seconds: int, **extra: object) -> dict[str, object]:
    payload = {
        "id": f"moment_{kind}_{offset_seconds}",
        "kind": kind,
        "occurred_at": _ts(offset_seconds),
        "camera_id": "camera-1",
        "camera_name": "Lobby Cam",
        "zone": "Lobby",
        "image_url": "",
        "thumbnail_url": "",
        "selection_reason": "empty_placeholder",
    }
    payload.update(extra)
    return payload


def _segment(start_seconds: int, duration_seconds: int, **extra: object) -> dict[str, object]:
    entered_at = _ts(start_seconds)
    exited_at = _ts(start_seconds + duration_seconds)
    payload = {
        "segment_id": f"segment_{start_seconds}",
        "entered_at": entered_at,
        "exited_at": exited_at,
        "camera_id": "camera-1",
        "camera_name": "Lobby Cam",
        "zone": "Lobby",
    }
    payload.update(extra)
    return payload


def _normalize_moments(moments: list[dict[str, object]]) -> list[tuple[str, str, str]]:
    normalized: list[tuple[str, str, str]] = []
    for moment in moments:
        occurred_at = moment["occurred_at"]
        if hasattr(occurred_at, "isoformat"):
            occurred_at = occurred_at.isoformat()
        normalized.append((str(moment["kind"]), str(occurred_at), str(moment["id"])))
    return normalized


def test_select_structural_moments_includes_required_milestones():
    moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("camera_transition", 42, to_camera_name="Second Cam"),
        _moment("zone_transition", 84, to_zone="South Hall"),
        _moment("alert_moment", 126, alert_type="unknown_person"),
        _moment("last_seen", 168, selection_reason="latest_frame_in_person_history"),
        _moment("dwell_checkpoint", 90),
    ]

    selected = select_structural_moments(moments)

    kinds = [moment["kind"] for moment in selected]
    assert kinds == [
        "first_seen",
        "camera_transition",
        "zone_transition",
        "alert_moment",
        "last_seen",
    ]
    assert "dwell_checkpoint" not in kinds


def test_select_dwell_checkpoints_only_emits_fifteen_second_marks_for_long_segments():
    segments = [
        _segment(0, 14),
        _segment(120, 65),
    ]

    checkpoints = select_dwell_checkpoints(segments)

    assert _normalize_moments(checkpoints) == [
        ("dwell_checkpoint", _ts(135).isoformat(), "segment_120_15"),
        ("dwell_checkpoint", _ts(150).isoformat(), "segment_120_30"),
        ("dwell_checkpoint", _ts(165).isoformat(), "segment_120_45"),
        ("dwell_checkpoint", _ts(180).isoformat(), "segment_120_60"),
    ]


def test_select_structural_moments_prefers_alert_or_snapshot_evidence_over_placeholders():
    moments = [
        _moment("alert_moment", 126, image_url="", thumbnail_url="", selection_reason="empty_placeholder"),
        _moment(
            "alert_moment",
            126,
            image_url="https://cdn.example.com/alert-evidence.jpg",
            thumbnail_url="https://cdn.example.com/alert-thumb.jpg",
            selection_reason="alert_evidence",
        ),
        _moment(
            "alert_moment",
            126,
            image_url="https://cdn.example.com/snapshot.jpg",
            thumbnail_url="https://cdn.example.com/snapshot-thumb.jpg",
            selection_reason="snapshot_evidence",
        ),
    ]

    selected = select_structural_moments(moments)

    assert len(selected) == 1
    assert selected[0]["kind"] == "alert_moment"
    assert selected[0]["selection_reason"] in {"alert_evidence", "snapshot_evidence"}
    assert selected[0]["image_url"] in {
        "https://cdn.example.com/alert-evidence.jpg",
        "https://cdn.example.com/snapshot.jpg",
    }


def test_dedupe_moments_drops_redundant_repeated_moments():
    moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("camera_transition", 42, to_camera_name="Second Cam"),
        _moment("camera_transition", 42, to_camera_name="Second Cam"),
        _moment("alert_moment", 126, alert_type="unknown_person"),
    ]

    deduped = dedupe_moments(moments)

    assert _normalize_moments(deduped) == [
        ("first_seen", _ts(0).isoformat(), "moment_first_seen_0"),
        ("camera_transition", _ts(42).isoformat(), "moment_camera_transition_42"),
        ("alert_moment", _ts(126).isoformat(), "moment_alert_moment_126"),
    ]


def test_build_storyboard_uses_curated_moments_and_stays_compact():
    curated_moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("dwell_checkpoint", 15),
        _moment("dwell_checkpoint", 30),
        _moment("camera_transition", 42, to_camera_name="Second Cam"),
        _moment("zone_transition", 84, to_zone="South Hall"),
        _moment("alert_moment", 126, alert_type="unknown_person"),
        _moment("dwell_checkpoint", 150),
        _moment("dwell_checkpoint", 165),
        _moment("last_seen", 180, selection_reason="latest_frame_in_person_history"),
    ]

    storyboard = build_storyboard(curated_moments)

    assert 4 <= len(storyboard) <= 8
    assert len(storyboard) < len(curated_moments)
    assert {item["id"] for item in storyboard}.issubset({moment["id"] for moment in curated_moments})
    assert {item["kind"] for item in storyboard}.issubset(
        {
            "first_seen",
            "dwell_checkpoint",
            "camera_transition",
            "zone_transition",
            "alert_moment",
            "last_seen",
        }
    )


def test_build_unknown_person_timeline_curates_structural_moments_and_dwell_checkpoints():
    moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("dwell_checkpoint", 15),
        _moment("camera_transition", 30, to_camera_name="Second Cam"),
        _moment("alert_moment", 45, image_url="", thumbnail_url="", selection_reason="empty_placeholder"),
        _moment(
            "alert_moment",
            45,
            image_url="https://cdn.example.com/alert-evidence.jpg",
            thumbnail_url="https://cdn.example.com/alert-thumb.jpg",
            selection_reason="alert_evidence",
        ),
        _moment("last_seen", 60, selection_reason="latest_frame_in_person_history"),
    ]
    segments = [
        _segment(0, 60),
    ]

    timeline = build_unknown_person_timeline(moments, dwell_checkpoints=select_dwell_checkpoints(segments))

    assert _normalize_moments(timeline) == [
        ("first_seen", _ts(0).isoformat(), "moment_first_seen_0"),
        ("dwell_checkpoint", _ts(15).isoformat(), "segment_0_15"),
        ("camera_transition", _ts(30).isoformat(), "moment_camera_transition_30"),
        ("dwell_checkpoint", _ts(30).isoformat(), "segment_0_30"),
        ("alert_moment", _ts(45).isoformat(), "moment_alert_moment_45"),
        ("dwell_checkpoint", _ts(45).isoformat(), "segment_0_45"),
        ("last_seen", _ts(60).isoformat(), "moment_last_seen_60"),
        ("dwell_checkpoint", _ts(60).isoformat(), "segment_0_60"),
    ]
    assert "moment_dwell_checkpoint_15" not in {item["id"] for item in timeline}


def test_build_storyboard_backfills_from_curated_moments_when_structural_moments_are_sparse():
    curated_moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("dwell_checkpoint", 15),
        _moment("camera_transition", 30, to_camera_name="Second Cam"),
        _moment("dwell_checkpoint", 45),
        _moment("last_seen", 60, selection_reason="latest_frame_in_person_history"),
    ]

    storyboard = build_storyboard(curated_moments)

    assert len(storyboard) == 5
    assert {item["kind"] for item in storyboard} == {
        "first_seen",
        "camera_transition",
        "last_seen",
        "dwell_checkpoint",
    }


def test_build_storyboard_backfills_supporting_evidence_when_structural_anchors_are_common():
    curated_moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("camera_transition", 30, to_camera_name="Second Cam"),
        _moment("zone_transition", 60, to_zone="South Hall"),
        _moment("alert_moment", 90, alert_type="unknown_person"),
        _moment("last_seen", 120, selection_reason="latest_frame_in_person_history"),
        _moment("dwell_checkpoint", 15),
        _moment("dwell_checkpoint", 45),
        _moment("dwell_checkpoint", 75),
        _moment("dwell_checkpoint", 105),
    ]

    storyboard = build_storyboard(curated_moments)

    assert len(storyboard) == 8
    assert any(item["kind"] == "dwell_checkpoint" for item in storyboard)
    assert {item["id"] for item in storyboard}.issubset({moment["id"] for moment in curated_moments})


def test_derive_continuity_confidence_scores_curated_timeline_output():
    moments = [
        _moment("first_seen", 0, selection_reason="first_frame_in_person_history"),
        _moment("camera_transition", 30, to_camera_name="Second Cam"),
        _moment("zone_transition", 60, to_zone="South Hall"),
        _moment("alert_moment", 90, alert_type="unknown_person"),
        _moment("last_seen", 120, selection_reason="latest_frame_in_person_history"),
    ]
    dwell_checkpoints = select_dwell_checkpoints([_segment(0, 60)])
    timeline = build_unknown_person_timeline(moments, dwell_checkpoints=dwell_checkpoints)

    confidence = derive_continuity_confidence(timeline)

    assert 0.0 <= confidence <= 1.0
    assert confidence > derive_continuity_confidence(build_unknown_person_timeline(moments[:2], dwell_checkpoints=[]))


def test_select_person_evidence_snapshots_prefers_exact_binding_track_matches():
    person = type(
        "Person",
        (),
        {
            "id": "person-1",
            "first_seen_at": _ts(0),
            "last_seen_at": _ts(60),
            "current_cameras": ["camera-1"],
        },
    )()
    binding = type("Binding", (), {"camera_id": "camera-1", "track_id": 275})()
    snapshots = [
        type(
            "Snapshot",
            (),
            {
                "id": "s1",
                "session_person_id": None,
                "camera_id": "camera-1",
                "full_frame_url": "snapshots/session/camera-1/20260404_150847_275_full.jpg",
                "face_crop_url": None,
                "body_crop_url": None,
                "created_at": _ts(47),
            },
        )(),
        type(
            "Snapshot",
            (),
            {
                "id": "s2",
                "session_person_id": None,
                "camera_id": "camera-1",
                "full_frame_url": "snapshots/session/camera-1/20260404_150847_999_full.jpg",
                "face_crop_url": None,
                "body_crop_url": None,
                "created_at": _ts(48),
            },
        )(),
    ]

    selected = select_person_evidence_snapshots(
        person=person,
        snapshots=snapshots,
        bindings=[binding],
        dwell_segments=[],
    )

    assert [snapshot.id for snapshot in selected] == ["s1"]


def test_select_person_evidence_snapshots_falls_back_to_nearest_same_camera_timeline_frames():
    person = type(
        "Person",
        (),
        {
            "id": "person-1",
            "first_seen_at": _ts(0),
            "last_seen_at": _ts(60),
            "current_cameras": ["camera-1"],
        },
    )()
    dwell_segments = [
        type(
            "Segment",
            (),
            {
                "camera_id": "camera-1",
                "entered_at": _ts(0),
                "exited_at": _ts(60),
            },
        )()
    ]
    snapshots = [
        type(
            "Snapshot",
            (),
            {
                "id": "near-first",
                "session_person_id": None,
                "camera_id": "camera-1",
                "full_frame_url": "snapshots/session/camera-1/20260404_150800_200_full.jpg",
                "face_crop_url": None,
                "body_crop_url": None,
                "created_at": _ts(5),
            },
        )(),
        type(
            "Snapshot",
            (),
            {
                "id": "near-last",
                "session_person_id": None,
                "camera_id": "camera-1",
                "full_frame_url": "snapshots/session/camera-1/20260404_150855_201_full.jpg",
                "face_crop_url": None,
                "body_crop_url": None,
                "created_at": _ts(55),
            },
        )(),
        type(
            "Snapshot",
            (),
            {
                "id": "wrong-camera",
                "session_person_id": None,
                "camera_id": "camera-2",
                "full_frame_url": "snapshots/session/camera-2/20260404_150855_300_full.jpg",
                "face_crop_url": None,
                "body_crop_url": None,
                "created_at": _ts(55),
            },
        )(),
    ]

    selected = select_person_evidence_snapshots(
        person=person,
        snapshots=snapshots,
        bindings=[],
        dwell_segments=dwell_segments,
    )

    assert [snapshot.id for snapshot in selected] == ["near-first", "near-last"]
