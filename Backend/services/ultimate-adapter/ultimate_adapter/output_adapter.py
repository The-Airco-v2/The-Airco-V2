"""Normalize Ultimate core updates onto the existing v2 Redis event contracts."""

from __future__ import annotations

import base64
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.events import CropEventPayload, IdentityEventPayload, SnapshotEventPayload, StreamNames, TrackEventPayload
from airco.redis_streams import publish_event
from ultimate_adapter.id_bridge import UltimateCanonicalIdBridge
from ultimate_adapter.ultimate_core import UltimateCleanupResult, UltimateFrameResult


def _coerce_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _bbox_to_list(bbox: dict[str, int]) -> list[float]:
    return [
        float(bbox["x1"]),
        float(bbox["y1"]),
        float(bbox["x2"]),
        float(bbox["y2"]),
    ]


class UltimateOutputAdapter:
    """Bridge Ultimate updates into the same tracks/identity streams used today."""

    def __init__(
        self,
        *,
        redis_client,
        bridge: UltimateCanonicalIdBridge | None = None,
        publisher=publish_event,
        snapshot_interval_frames: int = 30,
    ):
        self.redis = redis_client
        self.bridge = bridge or UltimateCanonicalIdBridge(redis_client)
        self.publisher = publisher
        self._active_tracks: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.snapshot_interval_frames = max(1, int(snapshot_interval_frames))

    def _encode_jpeg_b64(self, image) -> str | None:
        import cv2

        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            return None
        return base64.b64encode(encoded.tobytes()).decode("ascii")

    def _crop_frame(self, frame, bbox: dict[str, int]):
        if frame is None:
            return None
        height, width = frame.shape[:2]
        x1 = max(0, min(width, int(bbox["x1"])))
        x2 = max(0, min(width, int(bbox["x2"])))
        y1 = max(0, min(height, int(bbox["y1"])))
        y2 = max(0, min(height, int(bbox["y2"])))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _crop_face_region(self, frame, bbox: dict[str, int]):
        body_crop = self._crop_frame(frame, bbox)
        if body_crop is None:
            return None
        height = body_crop.shape[0]
        face_height = max(0, int(height * 0.4))
        if face_height < 8:
            return None
        face_crop = body_crop[:face_height, :]
        if face_crop.size == 0:
            return None
        return face_crop

    async def publish_frame_result(
        self,
        db,
        *,
        result: UltimateFrameResult,
        frame=None,
        observed_at: datetime | None = None,
    ) -> None:
        observed_at = observed_at or datetime.now(timezone.utc)
        session_id = _coerce_uuid(result.session_id)
        camera_id = _coerce_uuid(result.camera_id)
        state_key = (str(session_id), str(camera_id))
        previous_track_ids = set(self._active_tracks.get(state_key, set()))
        current_track_ids: set[int] = set()
        full_frame_b64 = self._encode_jpeg_b64(frame) if frame is not None else None

        for update in result.updates:
            track_id = update.track.track_id
            if track_id is None:
                continue

            current_track_ids.add(int(track_id))
            resolution = await self.bridge.record_track_observation(
                db,
                update=update,
                observed_at=observed_at,
            )
            if resolution is None:
                continue

            await self.publisher(
                StreamNames.TRACKS,
                TrackEventPayload(
                    event_type=resolution.track_event_type,
                    session_id=session_id,
                    camera_id=camera_id,
                    track_id=int(track_id),
                    bbox=_bbox_to_list(update.track.bbox),
                    confidence=float(update.track.confidence),
                    frame_number=int(result.frame_index),
                    timestamp=observed_at,
                ).to_redis(),
            )

            crop = self._crop_frame(frame, update.track.bbox)
            crop_b64 = self._encode_jpeg_b64(crop) if crop is not None else None
            if crop_b64:
                await self.publisher(
                    StreamNames.CROPS,
                    CropEventPayload(
                        event_type="body_crop",
                        session_id=session_id,
                        camera_id=camera_id,
                        track_id=int(track_id),
                        crop_b64=crop_b64,
                        bbox=_bbox_to_list(update.track.bbox),
                        quality_score=float(update.track.confidence),
                        timestamp=observed_at,
                    ).to_redis(),
                )
            face_crop = self._crop_face_region(frame, update.track.bbox)
            face_crop_b64 = self._encode_jpeg_b64(face_crop) if face_crop is not None else None
            if face_crop_b64:
                await self.publisher(
                    StreamNames.CROPS,
                    CropEventPayload(
                        event_type="face_crop",
                        session_id=session_id,
                        camera_id=camera_id,
                        track_id=int(track_id),
                        crop_b64=face_crop_b64,
                        bbox=_bbox_to_list(update.track.bbox),
                        quality_score=float(update.track.confidence),
                        timestamp=observed_at,
                    ).to_redis(),
                )

            should_publish_snapshot = (
                full_frame_b64 is not None
                and (
                    resolution.track_event_type == "track_started"
                    or int(result.frame_index) % self.snapshot_interval_frames == 0
                )
            )
            if should_publish_snapshot:
                await self.publisher(
                    StreamNames.SNAPSHOTS,
                    SnapshotEventPayload(
                        event_type="snapshot_requested",
                        session_id=session_id,
                        camera_id=camera_id,
                        track_id=int(track_id),
                        session_person_id=resolution.session_person_id,
                        trigger="identity",
                        full_frame_b64=full_frame_b64,
                        bbox=_bbox_to_list(update.track.bbox),
                        label="Ultimate RE-ID",
                        timestamp=observed_at,
                    ).to_redis(),
                )

            if resolution.person_created:
                await self.publisher(
                    StreamNames.IDENTITY,
                    IdentityEventPayload(
                        event_type="person_created",
                        session_id=session_id,
                        session_person_id=resolution.session_person_id,
                        recognition_state=resolution.recognition_state,
                        track_id=int(track_id),
                        camera_id=camera_id,
                        timestamp=observed_at,
                    ).to_redis(),
                )

        ended_track_ids = previous_track_ids - current_track_ids
        for ended_track_id in sorted(ended_track_ids):
            closure = await self.bridge.close_track(
                db,
                session_id=session_id,
                camera_id=camera_id,
                track_id=int(ended_track_id),
                observed_at=observed_at,
            )
            if not closure.closed:
                continue
            await self.publisher(
                StreamNames.TRACKS,
                TrackEventPayload(
                    event_type="track_ended",
                    session_id=session_id,
                    camera_id=camera_id,
                    track_id=int(ended_track_id),
                    bbox=[],
                    confidence=0.0,
                    frame_number=int(result.frame_index),
                    timestamp=observed_at,
                ).to_redis(),
            )

        self._active_tracks[state_key] = current_track_ids

    async def publish_cleanup(
        self,
        db,
        *,
        result: UltimateCleanupResult,
        observed_at: datetime | None = None,
    ) -> None:
        observed_at = observed_at or datetime.now(timezone.utc)
        session_id = _coerce_uuid(result.session_id)
        camera_id = _coerce_uuid(result.camera_id)
        state_key = (str(session_id), str(camera_id))

        for track_id in sorted(self._active_tracks.get(state_key, set())):
            closure = await self.bridge.close_track(
                db,
                session_id=session_id,
                camera_id=camera_id,
                track_id=int(track_id),
                observed_at=observed_at,
            )
            if not closure.closed:
                continue
            await self.publisher(
                StreamNames.TRACKS,
                TrackEventPayload(
                    event_type="track_ended",
                    session_id=session_id,
                    camera_id=camera_id,
                    track_id=int(track_id),
                    bbox=[],
                    confidence=0.0,
                    frame_number=int(result.frame_index),
                    timestamp=observed_at,
                ).to_redis(),
            )

        self._active_tracks.pop(state_key, None)
