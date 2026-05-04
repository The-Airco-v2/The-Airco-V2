"""Pure helpers for curating unknown-person evidence timelines."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

Moment = Mapping[str, Any]
Segment = Mapping[str, Any]

STRUCTURAL_KIND_ORDER = (
    "first_seen",
    "camera_transition",
    "zone_transition",
    "alert_moment",
    "last_seen",
)

KIND_SORT_PRIORITY = {
    "first_seen": 0,
    "camera_transition": 1,
    "zone_transition": 2,
    "alert_moment": 3,
    "last_seen": 4,
    "dwell_checkpoint": 5,
}

SNAPSHOT_TRACK_ID_RE = re.compile(r"_(?P<track_id>\d+)_full\.(?:jpe?g|png)$", re.IGNORECASE)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _moment_time(moment: Moment) -> datetime:
    value = _as_datetime(moment.get("occurred_at"))
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _moment_sort_key(moment: Moment) -> tuple[datetime, str, str]:
    return (
        _moment_time(moment),
        f"{KIND_SORT_PRIORITY.get(_text(moment.get('kind')), 99):02d}",
        str(moment.get("id", "")),
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _camera_details(camera_id: Any, cameras_by_id: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    normalized_camera_id = _text(camera_id) or None
    camera = cameras_by_id.get(normalized_camera_id or "")
    return normalized_camera_id, getattr(camera, "name", None), getattr(camera, "zone", None)


def _selected_camera_id(person: Any) -> str | None:
    current_cameras = getattr(person, "current_cameras", None) or []
    ordered_camera_ids = sorted({_text(camera_id) for camera_id in current_cameras if _text(camera_id)})
    return ordered_camera_ids[0] if ordered_camera_ids else None


def _selected_camera_context(person: Any, cameras_by_id: Mapping[str, Any]) -> tuple[str | None, str | None]:
    camera_id = _selected_camera_id(person)
    _normalized_camera_id, camera_name, zone = _camera_details(camera_id, cameras_by_id)
    return camera_name, zone


def _snapshot_image_url(snapshot: Any) -> str | None:
    if snapshot is None:
        return None
    return (
        getattr(snapshot, "face_crop_url", None)
        or getattr(snapshot, "body_crop_url", None)
        or getattr(snapshot, "full_frame_url", None)
    )


def _snapshot_track_id(snapshot: Any) -> int | None:
    image_url = _snapshot_image_url(snapshot) or _text(getattr(snapshot, "full_frame_url", None))
    if not image_url:
        return None
    match = SNAPSHOT_TRACK_ID_RE.search(image_url)
    if not match:
        return None
    try:
        return int(match.group("track_id"))
    except (TypeError, ValueError):
        return None


def _snapshot_key(snapshot: Any) -> tuple[str, str, int | None, str]:
    return (
        _text(getattr(snapshot, "id", "")),
        _text(getattr(snapshot, "camera_id", "")),
        _snapshot_track_id(snapshot),
        _text(getattr(snapshot, "full_frame_url", "")),
    )


def select_person_evidence_snapshots(
    *,
    person: Any,
    snapshots: Sequence[Any],
    bindings: Sequence[Any] | None = None,
    dwell_segments: Sequence[Any] | None = None,
    fallback_gap_seconds: float = 20.0,
) -> list[Any]:
    """Recover representative snapshots for a canonical person.

    Prefer directly linked or exact track-binding matches. If those are sparse,
    supplement with nearest same-camera timeline snapshots so the evidence
    timeline is not empty while live binding linkage catches up.
    """

    person_id = _text(getattr(person, "id", None))
    binding_pairs = {
        (_text(getattr(binding, "camera_id", None)), getattr(binding, "track_id", None))
        for binding in (bindings or [])
        if _text(getattr(binding, "camera_id", None))
    }

    selected: list[Any] = []
    seen_keys: set[tuple[str, str, int | None, str]] = set()

    def include(snapshot: Any) -> None:
        key = _snapshot_key(snapshot)
        if key in seen_keys:
            return
        seen_keys.add(key)
        selected.append(snapshot)

    for snapshot in snapshots:
        if _text(getattr(snapshot, "session_person_id", None)) == person_id:
            include(snapshot)

    for snapshot in snapshots:
        pair = (_text(getattr(snapshot, "camera_id", None)), _snapshot_track_id(snapshot))
        if pair in binding_pairs:
            include(snapshot)

    if selected:
        return sorted(
            selected,
            key=lambda snapshot: (
                getattr(snapshot, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
                _text(getattr(snapshot, "id", "")),
            ),
        )

    anchors: list[tuple[datetime, str | None]] = []
    first_seen_at = _as_datetime(getattr(person, "first_seen_at", None))
    last_seen_at = _as_datetime(getattr(person, "last_seen_at", None))
    selected_camera_id = _selected_camera_id(person)
    if first_seen_at is not None:
        anchors.append((first_seen_at, selected_camera_id))
    if last_seen_at is not None and last_seen_at != first_seen_at:
        anchors.append((last_seen_at, selected_camera_id))

    for segment in dwell_segments or []:
        entered_at = _as_datetime(getattr(segment, "entered_at", None))
        exited_at = _as_datetime(getattr(segment, "exited_at", None))
        camera_id = _text(getattr(segment, "camera_id", None)) or None
        if entered_at is not None:
            anchors.append((entered_at, camera_id))
        if exited_at is not None:
            anchors.append((exited_at, camera_id))

    for anchor_time, camera_id in anchors:
        nearest_snapshot = None
        nearest_gap = None
        for snapshot in snapshots:
            snapshot_time = _as_datetime(getattr(snapshot, "created_at", None))
            snapshot_camera_id = _text(getattr(snapshot, "camera_id", None)) or None
            if snapshot_time is None:
                continue
            if camera_id and snapshot_camera_id and snapshot_camera_id != camera_id:
                continue
            gap_seconds = abs((snapshot_time - anchor_time).total_seconds())
            if gap_seconds > fallback_gap_seconds:
                continue
            if nearest_gap is None or gap_seconds < nearest_gap:
                nearest_snapshot = snapshot
                nearest_gap = gap_seconds
        if nearest_snapshot is not None:
            include(nearest_snapshot)

    return sorted(
        selected,
        key=lambda snapshot: (
            getattr(snapshot, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            _text(getattr(snapshot, "id", "")),
        ),
    )


def _evidence_score(moment: Moment) -> tuple[int, int, int, int]:
    selection_reason = _text(moment.get("selection_reason"))
    image_url = _text(moment.get("image_url"))
    thumbnail_url = _text(moment.get("thumbnail_url"))
    has_image = 1 if image_url else 0
    has_thumbnail = 1 if thumbnail_url else 0
    reason_bonus = 0
    if selection_reason and selection_reason != "empty_placeholder":
        reason_bonus = 2
    if selection_reason in {"alert_evidence", "snapshot_evidence"}:
        reason_bonus = 4
    return (reason_bonus, has_image, has_thumbnail, 1 if selection_reason else 0)


def _moment_signature(moment: Moment) -> tuple[Any, ...]:
    occurred_at = _as_datetime(moment.get("occurred_at"))
    if occurred_at is not None and occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return (
        _text(moment.get("kind")),
        occurred_at.isoformat() if occurred_at is not None else None,
        _text(moment.get("camera_id")),
        _text(moment.get("camera_name")),
        _text(moment.get("zone")),
        _text(moment.get("image_url")),
        _text(moment.get("thumbnail_url")),
        _text(moment.get("selection_reason")),
        _text(moment.get("to_camera_name")),
        _text(moment.get("to_zone")),
        _text(moment.get("alert_type")),
        _text(moment.get("segment_id")),
    )


def _support_sort_key(moment: Moment) -> tuple[int, datetime, str, str]:
    selection_reason = _text(moment.get("selection_reason"))
    image_url = _text(moment.get("image_url"))
    thumbnail_url = _text(moment.get("thumbnail_url"))
    kind = _text(moment.get("kind"))

    score = 0
    if kind == "dwell_checkpoint":
        score += 1
    if selection_reason and selection_reason != "empty_placeholder":
        score += 2
    if selection_reason in {"alert_evidence", "snapshot_evidence"}:
        score += 3
    if image_url:
        score += 2
    if thumbnail_url:
        score += 1

    return (-score, _moment_time(moment), kind, str(moment.get("id", "")))


def derive_continuity_reasons(
    timeline: Sequence[Moment] | None = None,
) -> list[str]:
    curated_timeline = list(timeline or [])
    kinds = {_text(moment.get("kind")) for moment in curated_timeline}
    reasons: list[str] = []
    if {"first_seen", "last_seen"}.issubset(kinds):
        reasons.append("first_seen_and_latest_seen")
    if "camera_transition" in kinds:
        reasons.append("camera_transition")
    if "zone_transition" in kinds:
        reasons.append("zone_transition")
    if any(_text(moment.get("selection_reason")) == "alert_evidence" for moment in curated_timeline):
        reasons.append("alert_evidence")
    if "dwell_checkpoint" in kinds:
        reasons.append("dwell_checkpoints")
    if not reasons and curated_timeline:
        reasons.append("curated_timeline")
    return reasons


def _dwell_checkpoint_inputs(
    dwell_segments: Sequence[Any],
    cameras_by_id: Mapping[str, Any],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for segment in dwell_segments:
        entered_at = getattr(segment, "entered_at", None)
        exited_at = getattr(segment, "exited_at", None)
        if entered_at is not None and exited_at is None:
            dwell_seconds = float(getattr(segment, "dwell_seconds", 0.0) or 0.0)
            if dwell_seconds > 0:
                exited_at = entered_at + timedelta(seconds=dwell_seconds)
        camera_id, camera_name, zone = _camera_details(getattr(segment, "camera_id", None), cameras_by_id)
        segments.append(
            {
                "segment_id": _text(getattr(segment, "id", None) or getattr(segment, "camera_id", "segment")),
                "entered_at": entered_at,
                "exited_at": exited_at,
                "camera_id": camera_id,
                "camera_name": camera_name,
                "zone": zone,
            }
        )
    return segments


def build_unknown_person_evidence_payload(
    *,
    person: Any,
    cameras_by_id: Mapping[str, Any],
    alerts: Sequence[Any],
    dwell_segments: Sequence[Any],
    snapshots: Sequence[Any],
) -> dict[str, Any]:
    selected_camera_id = _selected_camera_id(person)
    selected_camera_name, selected_zone = _selected_camera_context(person, cameras_by_id)
    sorted_snapshots = sorted(
        snapshots,
        key=lambda snapshot: (
            getattr(snapshot, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            _text(getattr(snapshot, "id", "")),
        ),
    )
    first_snapshot = sorted_snapshots[0] if sorted_snapshots else None
    last_snapshot = sorted_snapshots[-1] if sorted_snapshots else None
    evidence_moments: list[dict[str, Any]] = []

    first_seen_at = getattr(person, "first_seen_at", None)
    if first_seen_at is not None:
        first_camera_id, first_camera_name, first_zone = _camera_details(
            getattr(first_snapshot, "camera_id", None) or selected_camera_id,
            cameras_by_id,
        )
        first_image_url = _snapshot_image_url(first_snapshot) or getattr(person, "best_thumbnail_url", None)
        evidence_moments.append(
            {
                "id": f"first_seen:{getattr(person, 'id', 'unknown')}",
                "kind": "first_seen",
                "occurred_at": first_seen_at,
                "camera_id": first_camera_id,
                "camera_name": first_camera_name or selected_camera_name,
                "zone": first_zone or selected_zone,
                "image_url": first_image_url,
                "thumbnail_url": first_image_url,
                "selection_reason": "snapshot_evidence" if _snapshot_image_url(first_snapshot) else "first_frame_in_person_history",
            }
        )

    previous_snapshot = None
    for snapshot in sorted_snapshots:
        camera_id, camera_name, zone = _camera_details(getattr(snapshot, "camera_id", None), cameras_by_id)
        image_url = _snapshot_image_url(snapshot)
        selection_reason = "snapshot_evidence" if image_url else "empty_placeholder"
        if previous_snapshot is not None:
            previous_camera_id, _previous_camera_name, previous_zone = _camera_details(
                getattr(previous_snapshot, "camera_id", None),
                cameras_by_id,
            )
            if camera_id and camera_id != previous_camera_id:
                evidence_moments.append(
                    {
                        "id": f"camera_transition:{getattr(snapshot, 'id', 'snapshot')}",
                        "kind": "camera_transition",
                        "occurred_at": getattr(snapshot, "created_at", None),
                        "camera_id": camera_id,
                        "camera_name": camera_name,
                        "zone": zone,
                        "to_camera_name": camera_name,
                        "image_url": image_url,
                        "thumbnail_url": image_url,
                        "selection_reason": selection_reason,
                    }
                )
            if zone and zone != previous_zone:
                evidence_moments.append(
                    {
                        "id": f"zone_transition:{getattr(snapshot, 'id', 'snapshot')}",
                        "kind": "zone_transition",
                        "occurred_at": getattr(snapshot, "created_at", None),
                        "camera_id": camera_id,
                        "camera_name": camera_name,
                        "zone": zone,
                        "to_zone": zone,
                        "image_url": image_url,
                        "thumbnail_url": image_url,
                        "selection_reason": selection_reason,
                    }
                )
        previous_snapshot = snapshot

    for alert in sorted(
        alerts,
        key=lambda item: (
            getattr(item, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            _text(getattr(item, "id", "")),
        ),
    ):
        camera_id, camera_name, zone = _camera_details(getattr(alert, "camera_id", None), cameras_by_id)
        image_url = getattr(alert, "evidence_url", None)
        evidence_moments.append(
            {
                "id": f"alert:{getattr(alert, 'id', 'alert')}",
                "kind": "alert_moment",
                "occurred_at": getattr(alert, "created_at", None),
                "camera_id": camera_id,
                "camera_name": camera_name,
                "zone": zone,
                "alert_type": getattr(alert, "alert_type", None),
                "image_url": image_url,
                "thumbnail_url": image_url,
                "selection_reason": "alert_evidence" if image_url else "empty_placeholder",
            }
        )

    last_seen_at = getattr(person, "last_seen_at", None)
    if last_seen_at is not None:
        last_camera_id, last_camera_name, last_zone = _camera_details(
            getattr(last_snapshot, "camera_id", None) or selected_camera_id,
            cameras_by_id,
        )
        last_image_url = _snapshot_image_url(last_snapshot) or getattr(person, "best_thumbnail_url", None)
        evidence_moments.append(
            {
                "id": f"last_seen:{getattr(person, 'id', 'unknown')}",
                "kind": "last_seen",
                "occurred_at": last_seen_at,
                "camera_id": last_camera_id,
                "camera_name": last_camera_name or selected_camera_name,
                "zone": last_zone or selected_zone,
                "image_url": last_image_url,
                "thumbnail_url": last_image_url,
                "selection_reason": "snapshot_evidence" if _snapshot_image_url(last_snapshot) else "latest_frame_in_person_history",
            }
        )

    dwell_checkpoints = select_dwell_checkpoints(_dwell_checkpoint_inputs(dwell_segments, cameras_by_id))
    curated_timeline = build_unknown_person_timeline(evidence_moments, dwell_checkpoints=dwell_checkpoints)
    storyboard = build_storyboard(curated_timeline)

    window_start = first_seen_at or (curated_timeline[0].get("occurred_at") if curated_timeline else None)
    window_end = last_seen_at or (curated_timeline[-1].get("occurred_at") if curated_timeline else None)

    return {
        "timeline": {
            "window_start": window_start,
            "window_end": window_end,
            "moments": [dict(moment) for moment in curated_timeline],
        },
        "storyboard": [dict(moment) for moment in storyboard],
        "continuity_confidence": derive_continuity_confidence(curated_timeline),
        "continuity_reasons": derive_continuity_reasons(curated_timeline),
    }


def dedupe_moments(moments: Sequence[Moment]) -> list[dict[str, Any]]:
    """Drop exact repeated moments while preserving order."""

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for moment in moments:
        signature = _moment_signature(moment)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(dict(moment))
    return deduped


def select_structural_moments(moments: Sequence[Moment]) -> list[dict[str, Any]]:
    """Select the required storyline anchors, preferring richer evidence."""

    best_by_kind: dict[str, tuple[tuple[int, int, int, int], int, dict[str, Any]]] = {}
    for index, moment in enumerate(moments):
        kind = _text(moment.get("kind"))
        if kind not in STRUCTURAL_KIND_ORDER:
            continue
        candidate = dict(moment)
        score = _evidence_score(candidate)
        existing = best_by_kind.get(kind)
        if existing is None or score > existing[0] or (score == existing[0] and index < existing[1]):
            best_by_kind[kind] = (score, index, candidate)

    selected = [best_by_kind[kind][2] for kind in STRUCTURAL_KIND_ORDER if kind in best_by_kind]
    return selected


def select_dwell_checkpoints(segments: Sequence[Segment]) -> list[dict[str, Any]]:
    """Emit 15-second dwell checkpoints for segments that last long enough."""

    checkpoints: list[dict[str, Any]] = []
    for segment in segments:
        entered_at = _as_datetime(segment.get("entered_at"))
        exited_at = _as_datetime(segment.get("exited_at"))
        if entered_at is None or exited_at is None:
            continue
        duration_seconds = (exited_at - entered_at).total_seconds()
        if duration_seconds < 15:
            continue

        offset = 15
        segment_id = _text(segment.get("segment_id")) or "segment"
        while offset <= duration_seconds:
            checkpoints.append(
                {
                    "id": f"{segment_id}_{offset}",
                    "kind": "dwell_checkpoint",
                    "occurred_at": entered_at + timedelta(seconds=offset),
                    "segment_id": _text(segment.get("segment_id")) or None,
                    "camera_id": _text(segment.get("camera_id")) or None,
                    "camera_name": _text(segment.get("camera_name")) or None,
                    "zone": _text(segment.get("zone")) or None,
                }
            )
            offset += 15

    checkpoints.sort(key=_moment_sort_key)
    return checkpoints


def build_storyboard(curated_moments: Sequence[Moment]) -> list[dict[str, Any]]:
    """Build a compact narrative view from curated moments."""

    moments = dedupe_moments(curated_moments)
    selected = select_structural_moments(moments)
    selected_signatures = {_moment_signature(moment) for moment in selected}

    target_size = min(8, len(moments))
    if len(selected) < target_size:
        support_candidates = [
            moment
            for moment in moments
            if _moment_signature(moment) not in selected_signatures
        ]
        support_candidates.sort(key=_support_sort_key)

        for moment in support_candidates:
            if len(selected) >= target_size:
                break
            selected.append(dict(moment))

    selected.sort(key=_moment_sort_key)

    return selected


def build_unknown_person_timeline(
    moments: Sequence[Moment] | None = None,
    *,
    dwell_checkpoints: Sequence[Moment] | None = None,
) -> list[dict[str, Any]]:
    """Curate a timeline from structural moments plus explicit dwell checkpoints."""

    deduped_moments = dedupe_moments(moments or [])
    structural_moments = select_structural_moments(deduped_moments)
    structural_signatures = {_moment_signature(moment) for moment in structural_moments}
    explicit_dwell_checkpoints = dedupe_moments(dwell_checkpoints or [])

    timeline = list(structural_moments)

    if dwell_checkpoints is None:
        timeline.extend(
            moment
            for moment in deduped_moments
            if _text(moment.get("kind")) == "dwell_checkpoint"
        )
    else:
        timeline.extend(explicit_dwell_checkpoints)

    support_candidates = [
        moment
        for moment in deduped_moments
        if _moment_signature(moment) not in structural_signatures
        and _text(moment.get("kind")) != "dwell_checkpoint"
    ]
    support_candidates.sort(key=_support_sort_key)

    target_size = min(8, len(deduped_moments) + len(explicit_dwell_checkpoints))
    target_size = max(target_size, len(timeline))
    for moment in support_candidates:
        if len(timeline) >= target_size:
            break
        timeline.append(dict(moment))

    timeline = dedupe_moments(timeline)
    timeline.sort(key=_moment_sort_key)
    return timeline


def derive_continuity_confidence(
    timeline: Sequence[Moment] | None = None,
) -> float:
    """Return a lightweight confidence score for a curated timeline."""

    curated_timeline = list(timeline or [])
    if not curated_timeline:
        return 0.0

    structural_kinds = {
        _text(moment.get("kind"))
        for moment in curated_timeline
        if _text(moment.get("kind")) in STRUCTURAL_KIND_ORDER
    }
    checkpoint_count = sum(1 for moment in curated_timeline if _text(moment.get("kind")) == "dwell_checkpoint")
    supporting_count = sum(
        1
        for moment in curated_timeline
        if _text(moment.get("kind")) not in STRUCTURAL_KIND_ORDER and _text(moment.get("kind")) != "dwell_checkpoint"
    )

    structural_score = len(structural_kinds) / float(len(STRUCTURAL_KIND_ORDER))
    checkpoint_bonus = min(checkpoint_count, 6) / 20.0
    support_bonus = min(supporting_count, 4) / 40.0
    score = 0.2 + (0.55 * structural_score) + checkpoint_bonus + support_bonus
    return round(min(score, 1.0), 3)


__all__ = [
    "build_storyboard",
    "build_unknown_person_evidence_payload",
    "build_unknown_person_timeline",
    "dedupe_moments",
    "derive_continuity_confidence",
    "derive_continuity_reasons",
    "select_person_evidence_snapshots",
    "select_dwell_checkpoints",
    "select_structural_moments",
]
