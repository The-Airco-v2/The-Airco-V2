"""Custom Savant sink that publishes detection events to Redis Streams.

This is the bridge between the GPU pipeline and the event-driven backend.
Produces to: airco:tracks, airco:crops, airco:phones, airco:snapshots
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import redis
from savant.deepstream.pyfunc import NvDsPyFuncPlugin

from event_utils import is_full_frame_detection, normalize_track_id
from frame_artifacts import pop_track_artifacts

logger = logging.getLogger(__name__)


class RedisStreamSink(NvDsPyFuncPlugin):
    """Publishes detection events from Savant pipeline to Redis Streams."""

    STREAM_TRACKS = "airco:tracks"
    STREAM_CROPS = "airco:crops"
    STREAM_PHONES = "airco:phones"
    STREAM_SNAPSHOTS = "airco:snapshots"
    MAXLEN = 10000

    def __init__(self, redis_url: str = "redis://redis:6379/0"):
        super().__init__()
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._session_id = os.environ.get("SESSION_ID", str(uuid.uuid4()))
        self._fallback_camera_id = None
        self._active_tracks: dict[str, set[int]] = {}  # camera_id -> set of active track_ids
        self._context_refresh_deadline = 0.0
        logger.info(f"Redis sink initialized, session={self._session_id}")

    def process_frame(self, buffer, frame_meta):
        """Called by Savant for each processed frame."""
        source_id = getattr(frame_meta, 'source_id', 'unknown')
        frame_number = int(getattr(frame_meta, 'frame_num', 0))
        self._refresh_context()
        now = datetime.now(timezone.utc).isoformat()

        current_tracks = set()

        for obj_meta in self._get_objects(frame_meta):
            if getattr(obj_meta, "is_primary", False):
                continue
            raw_track_id = getattr(obj_meta, "track_id", -1)
            track_id = normalize_track_id(raw_track_id)
            if track_id is None:
                if raw_track_id not in (-1, None):
                    logger.warning(
                        "Dropping invalid track id from Savant object metadata: source=%s frame=%s raw_track_id=%s",
                        source_id,
                        frame_number,
                        raw_track_id,
                    )
                continue

            current_tracks.add(track_id)
            bbox = self._get_bbox(obj_meta)
            if is_full_frame_detection(bbox, frame_meta):
                continue
            confidence = getattr(obj_meta, 'confidence', 0.0)

            # Determine if track just started
            prev_tracks = self._active_tracks.get(source_id, set())
            event_type = "track_started" if track_id not in prev_tracks else "track_observed"
            event_camera_id = self._event_camera_id(source_id)
            event_session_id = self._session_id
            artifacts = pop_track_artifacts(
                source_id=str(source_id),
                frame_num=frame_number,
                track_id=int(track_id),
            )

            # Publish track event
            self._publish(self.STREAM_TRACKS, {
                "event_type": event_type,
                "session_id": event_session_id,
                "camera_id": event_camera_id,
                "track_id": str(track_id),
                "bbox": json.dumps(bbox) if bbox else "[]",
                "confidence": str(confidence),
                "timestamp": now,
                "frame_number": str(frame_number),
            })

            # Publish crops if attached by CropExtractor pyfunc
            obj_crops = getattr(obj_meta, "_airco_crops", None) or []
            cached_crops = artifacts.get("crops", [])
            for crop in [*obj_crops, *cached_crops]:
                    self._publish(self.STREAM_CROPS, {
                        "event_type": crop["type"],
                        "session_id": event_session_id,
                        "camera_id": event_camera_id,
                        "track_id": str(track_id),
                        "crop_b64": crop["b64"],
                        "bbox": json.dumps(crop["bbox"]),
                        "quality_score": str(crop["quality"]),
                        "timestamp": now,
                    })

            # Publish snapshot if attached
            snap = getattr(obj_meta, "_airco_snapshot", None) or artifacts.get("snapshot")
            if snap:
                self._publish(self.STREAM_SNAPSHOTS, {
                    "event_type": "snapshot_requested",
                    "session_id": event_session_id,
                    "camera_id": event_camera_id,
                    "track_id": str(track_id),
                    "trigger": "periodic",
                    "full_frame_b64": snap["full_b64"],
                    "bbox": json.dumps(snap["bbox"]),
                    "label": "",
                    "quality": str(snap.get("quality", 0)),
                    "timestamp": now,
                })

            # Check for phone detections (secondary classifier output)
            if hasattr(obj_meta, '_phone_confidence'):
                self._publish(self.STREAM_PHONES, {
                    "event_type": "phone_detected",
                    "session_id": event_session_id,
                    "camera_id": event_camera_id,
                    "track_id": str(track_id),
                    "confidence": str(obj_meta._phone_confidence),
                    "duration_seconds": "0",
                    "timestamp": now,
                })

        # Detect track_ended events
        prev_tracks = self._active_tracks.get(source_id, set())
        ended_tracks = prev_tracks - current_tracks
        for ended_id in ended_tracks:
            self._publish(self.STREAM_TRACKS, {
                "event_type": "track_ended",
                "session_id": self._session_id,
                "camera_id": self._event_camera_id(source_id),
                "track_id": str(ended_id),
                "bbox": "[]",
                "confidence": "0",
                "timestamp": now,
                "frame_number": str(frame_number),
            })

        self._active_tracks[source_id] = current_tracks

    def _refresh_context(self):
        now = time.monotonic()
        if now < self._context_refresh_deadline:
            return
        self._context_refresh_deadline = now + 1.0
        try:
            session_id = self._redis.get("airco:local:active_session_id")
            camera_id = self._redis.get("airco:local:active_camera_id")
            if session_id:
                self._session_id = session_id
            if camera_id:
                self._fallback_camera_id = camera_id
        except Exception:
            logger.exception("Failed to refresh local session context")

    def _event_camera_id(self, source_id) -> str:
        source = str(source_id)
        try:
            uuid.UUID(source)
            return source
        except (TypeError, ValueError, AttributeError):
            return self._fallback_camera_id or source

    def _publish(self, stream: str, fields: dict[str, str]):
        try:
            self._redis.xadd(stream, fields, maxlen=self.MAXLEN, approximate=True)
        except Exception:
            logger.exception(f"Failed to publish to {stream}")

    def _get_objects(self, frame_meta):
        if hasattr(frame_meta, 'objects'):
            return frame_meta.objects
        return []

    def _get_bbox(self, obj_meta) -> list[float] | None:
        if hasattr(obj_meta, 'bbox') and obj_meta.bbox is not None:
            left, top, right, bottom = obj_meta.bbox.as_ltrb()
            return [left, top, right, bottom]
        return None
