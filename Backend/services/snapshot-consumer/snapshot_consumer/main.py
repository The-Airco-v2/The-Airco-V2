"""Snapshot consumer -- renders evidence snapshots and uploads to MinIO."""

import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.db import async_session
from airco.events import StreamNames
from airco.minio_client import upload_bytes
from airco.redis_streams import consume_stream

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("snapshot-consumer")


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value in (None, "", "unknown"):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def _resolve_session_person_id(
    db,
    *,
    session_id: uuid.UUID | None,
    camera_id: uuid.UUID | None,
    track_id: int | None,
    event_session_person_id: str | uuid.UUID | None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.2,
    recent_binding_window_seconds: float = 120.0,
) -> uuid.UUID | None:
    direct_person_id = _parse_uuid(event_session_person_id)
    if direct_person_id is not None:
        return direct_person_id

    if session_id is None or camera_id is None or track_id is None:
        return None

    from sqlalchemy import select

    from airco.models import SessionPersonTrackBinding

    for attempt in range(max_attempts):
        result = await db.execute(
            select(SessionPersonTrackBinding.session_person_id)
            .where(
                SessionPersonTrackBinding.session_id == session_id,
                SessionPersonTrackBinding.camera_id == camera_id,
                SessionPersonTrackBinding.track_id == track_id,
                SessionPersonTrackBinding.binding_state == "active",
            )
            .order_by(SessionPersonTrackBinding.last_seen_at.desc())
            .limit(1)
        )
        person_id = result.scalar_one_or_none()
        if person_id is not None:
            return person_id
        if attempt < max_attempts - 1:
            await asyncio.sleep(retry_delay_seconds)

    recent_cutoff = datetime.now(timezone.utc) - timedelta(seconds=recent_binding_window_seconds)
    result = await db.execute(
        select(SessionPersonTrackBinding.session_person_id)
        .where(
            SessionPersonTrackBinding.session_id == session_id,
            SessionPersonTrackBinding.camera_id == camera_id,
            SessionPersonTrackBinding.track_id == track_id,
            SessionPersonTrackBinding.last_seen_at >= recent_cutoff,
        )
        .order_by(SessionPersonTrackBinding.last_seen_at.desc())
        .limit(1)
    )
    person_id = result.scalar_one_or_none()
    if person_id is not None:
        return person_id
    return None


async def _update_best_thumbnail(
    db,
    *,
    session_person_id: uuid.UUID | None,
    full_frame_url: str,
    score: float,
) -> None:
    if session_person_id is None:
        return

    from airco.models import SessionPerson

    person = await db.get(SessionPerson, session_person_id)
    if person is None:
        return

    evidence_summary = dict(getattr(person, "evidence_summary", None) or {})
    current_score = float(evidence_summary.get("best_thumbnail_score", -1.0) or -1.0)
    current_url = getattr(person, "best_thumbnail_url", None)

    if current_url and score < current_score:
        return

    person.best_thumbnail_url = full_frame_url
    evidence_summary["best_thumbnail_score"] = score
    evidence_summary["best_thumbnail_updated_at"] = datetime.now(timezone.utc).isoformat()
    person.evidence_summary = evidence_summary


async def main():
    async def handler(msg_id: str, fields: dict):
        import cv2
        import numpy as np
        from snapshot_consumer.renderer import encode_jpeg, render_evidence

        session_id = _parse_uuid(fields.get("session_id"))
        camera_id = _parse_uuid(fields.get("camera_id"))
        track_id_raw = fields.get("track_id")
        try:
            track_id = int(track_id_raw) if track_id_raw not in (None, "", "unknown") else None
        except (TypeError, ValueError):
            track_id = None
        label = fields.get("label", "")
        bbox_raw = fields.get("bbox", "[0, 0, 0, 0]")
        bbox = json.loads(bbox_raw) if isinstance(bbox_raw, str) else bbox_raw
        full_frame_b64 = fields.get("full_frame_b64", "")

        if not full_frame_b64:
            return

        frame_bytes = base64.b64decode(full_frame_b64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        annotated = render_evidence(frame, bbox, label=label)
        jpeg_bytes = encode_jpeg(annotated)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        object_name = f"snapshots/{session_id or 'unknown'}/{camera_id or 'unknown'}/{ts}_{track_id or '0'}_full.jpg"
        upload_bytes(object_name, jpeg_bytes)

        async with async_session() as db:
            from airco.models import Snapshot

            session_person_id = await _resolve_session_person_id(
                db,
                session_id=session_id,
                camera_id=camera_id,
                track_id=track_id,
                event_session_person_id=fields.get("session_person_id"),
            )
            score = float(fields.get("quality", 0) or 0)
            snap = Snapshot(
                session_id=session_id,
                session_person_id=session_person_id,
                camera_id=camera_id,
                event_type=fields.get("trigger", "alert"),
                full_frame_url=object_name,
                bbox=bbox,
                score=score,
            )
            db.add(snap)
            await _update_best_thumbnail(
                db,
                session_person_id=session_person_id,
                full_frame_url=object_name,
                score=score,
            )
            await db.commit()

        logger.info(f"Snapshot saved: {object_name}")

    logger.info("Snapshot consumer starting...")
    await consume_stream(
        stream=StreamNames.SNAPSHOTS,
        group="snapshot-group",
        consumer=f"snapshot-{os.getpid()}",
        handler=handler,
    )


if __name__ == "__main__":
    asyncio.run(main())
