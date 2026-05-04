"""Shared helpers for Savant event filtering."""

from __future__ import annotations

from typing import Any

MAX_SIGNED_TRACK_ID = (1 << 63) - 1


def normalize_track_id(value: Any) -> int | None:
    """Return a sane signed track id or ``None`` for invalid tracker sentinels.

    DeepStream/Savant occasionally surfaces tracker ids as wrapped uint64 values
    such as ``18446744073709551615`` (the ``-1`` sentinel) or other values above
    the signed 64-bit range. Those ids are not stable canonical track keys for
    the backend, so we drop them before they can fan out into Redis and the DB.
    """

    if isinstance(value, bool):
        return None

    try:
        track_id = int(value)
    except (TypeError, ValueError):
        return None

    if track_id < 0 or track_id > MAX_SIGNED_TRACK_ID:
        return None
    return track_id


def _frame_dimensions(frame_meta: Any) -> tuple[float, float] | None:
    for width_attr, height_attr in (
        ("source_frame_width", "source_frame_height"),
        ("frame_width", "frame_height"),
    ):
        width = getattr(frame_meta, width_attr, None)
        height = getattr(frame_meta, height_attr, None)
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            return float(width), float(height)
    return None


def is_full_frame_detection(
    bbox: list[float] | None,
    frame_meta: Any,
    coverage_threshold: float = 0.98,
) -> bool:
    if bbox is None or len(bbox) != 4:
        return False

    dims = _frame_dimensions(frame_meta)
    if dims is None:
        return False

    frame_width, frame_height = dims
    if frame_width <= 0 or frame_height <= 0:
        return False

    left, top, right, bottom = bbox
    box_width = max(0.0, right - left)
    box_height = max(0.0, bottom - top)
    coverage = (box_width * box_height) / (frame_width * frame_height)

    return left <= 1.0 and top <= 1.0 and coverage >= coverage_threshold
