"""Frame-local artifact bridge between sequential Savant pyfunc elements."""

from __future__ import annotations

from threading import Lock
from typing import Any

_LOCK = Lock()
_ARTIFACTS: dict[tuple[str, int, int], dict[str, Any]] = {}


def store_track_artifacts(
    *,
    source_id: str,
    frame_num: int,
    track_id: int,
    crops: list[dict[str, Any]] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    key = (str(source_id), int(frame_num), int(track_id))
    with _LOCK:
        payload = _ARTIFACTS.setdefault(key, {})
        if crops:
            payload["crops"] = list(crops)
        if snapshot:
            payload["snapshot"] = dict(snapshot)


def pop_track_artifacts(*, source_id: str, frame_num: int, track_id: int) -> dict[str, Any]:
    key = (str(source_id), int(frame_num), int(track_id))
    with _LOCK:
        return _ARTIFACTS.pop(key, {})
