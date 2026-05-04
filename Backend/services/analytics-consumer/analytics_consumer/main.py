"""Analytics consumer -- materializes dwell, attendance, alerts, phone, activity."""

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from airco.config import settings
from airco.db import async_session
from airco.events import AlertEventPayload, StreamNames
from airco.redis_streams import consume_multiple_streams, publish_event
from analytics_consumer.activity import ActivityTracker
from analytics_consumer.alerts import AlertGenerator
from analytics_consumer.attendance import AttendanceTracker
from analytics_consumer.dwell import DwellTracker
from analytics_consumer.phone_events import PhoneTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analytics-consumer")

GROUP = "analytics-group"
CONSUMER = f"analytics-{os.getpid()}"


def _parse_optional_uuid(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_optional_int(value) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _track_person_key(session_id: uuid.UUID, camera_id: uuid.UUID, track_id: int) -> tuple[uuid.UUID, uuid.UUID, int]:
    return (session_id, camera_id, int(track_id))


def _merge_track_binding(bindings: list[dict] | None, camera_id: uuid.UUID, track_id: int) -> list[dict]:
    normalized = list(bindings or [])
    binding = {"camera_id": str(camera_id), "track_id": int(track_id)}
    if binding not in normalized:
        normalized.append(binding)
    return normalized


def _remove_track_binding(bindings: list[dict] | None, camera_id: uuid.UUID, track_id: int) -> list[dict]:
    target_camera = str(camera_id)
    return [
        binding
        for binding in (bindings or [])
        if not (
            str(binding.get("camera_id")) == target_camera
            and int(binding.get("track_id", -1)) == int(track_id)
        )
    ]


def _current_cameras_from_bindings(bindings: list[dict] | None) -> list[str]:
    return sorted({str(binding.get("camera_id")) for binding in (bindings or []) if binding.get("camera_id")})


async def _find_existing_person_id(
    db,
    *,
    session_id: uuid.UUID,
    camera_id: uuid.UUID,
    track_id: int,
) -> uuid.UUID | None:
    from sqlalchemy import select

    from airco.models import SessionPersonTrackBinding

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
    return result.scalar_one_or_none()


async def _resolve_person_id(
    db,
    *,
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID],
    session_id: uuid.UUID,
    camera_id: uuid.UUID,
    track_id: int,
) -> uuid.UUID | None:
    key = _track_person_key(session_id, camera_id, track_id)
    person_id = track_to_person.get(key)
    if person_id is not None:
        return person_id

    person_id = await _find_existing_person_id(
        db,
        session_id=session_id,
        camera_id=camera_id,
        track_id=track_id,
    )
    if person_id is not None:
        track_to_person[key] = person_id
    return person_id


async def _load_session_person(db, person_id: uuid.UUID):
    from airco.models import SessionPerson

    return await db.get(SessionPerson, person_id)


async def _handle_track_event(
    db,
    *,
    dwell: DwellTracker,
    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID],
    session_id: uuid.UUID,
    camera_id: uuid.UUID,
    track_id: int,
    event_type: str,
    ts: datetime,
):
    key = _track_person_key(session_id, camera_id, track_id)
    person_id = await _resolve_person_id(
        db,
        track_to_person=track_to_person,
        session_id=session_id,
        camera_id=camera_id,
        track_id=track_id,
    )
    if person_id is None:
        logger.debug(
            "Skipping unmapped track event because no canonical SessionPerson exists yet: session=%s camera=%s track=%s event=%s",
            session_id,
            camera_id,
            track_id,
            event_type,
        )
        if event_type == "track_ended":
            track_to_person.pop(key, None)
        return None

    if event_type == "track_started":
        dwell.track_entered(person_id, camera_id, ts)
        return person_id

    if event_type == "track_ended":
        segment = dwell.track_exited(person_id, camera_id, ts)
        if segment:
            from airco.models import CameraPresenceSegment

            db.add(
                CameraPresenceSegment(
                    session_id=session_id,
                    session_person_id=person_id,
                    camera_id=camera_id,
                    entered_at=segment["entered_at"],
                    exited_at=segment["exited_at"],
                    dwell_seconds=segment["dwell_seconds"],
                )
            )
        track_to_person.pop(key, None)
        return person_id

    return person_id


async def main():
    dwell = DwellTracker()
    attendance = AttendanceTracker(entrance_cameras=set())
    alerts = AlertGenerator()
    phones = PhoneTracker()
    activity = ActivityTracker()

    track_to_person: dict[tuple[uuid.UUID, uuid.UUID, int], uuid.UUID] = {}

    async with async_session() as db:
        from sqlalchemy import select

        from airco.models import Camera

        result = await db.execute(select(Camera).where(Camera.is_entrance == True))
        for cam in result.scalars():
            attendance.entrance_cameras.add(cam.id)

        async def handler(stream: str, msg_id: str, fields: dict):
            try:
                event_type = fields.get("event_type", "")
                session_id = uuid.UUID(fields["session_id"]) if "session_id" in fields else None
                ts = datetime.now(timezone.utc)

                if stream == StreamNames.TRACKS:
                    track_id = _parse_optional_int(fields.get("track_id"))
                    camera_id = _parse_optional_uuid(fields.get("camera_id"))
                    if session_id and camera_id and track_id is not None:
                        await _handle_track_event(
                            db,
                            dwell=dwell,
                            track_to_person=track_to_person,
                            session_id=session_id,
                            camera_id=camera_id,
                            track_id=track_id,
                            event_type=event_type,
                            ts=ts,
                        )

                elif stream == StreamNames.IDENTITY:
                    person_id = _parse_optional_uuid(fields.get("session_person_id"))
                    emp_id = _parse_optional_uuid(fields.get("employee_id"))
                    state = fields.get("recognition_state", "")
                    camera_id = _parse_optional_uuid(fields.get("camera_id"))

                    id_track_id = _parse_optional_int(fields.get("track_id"))
                    if session_id and camera_id and id_track_id is not None and person_id:
                        track_to_person[_track_person_key(session_id, camera_id, id_track_id)] = person_id

                    if person_id:
                        session_person = await _load_session_person(db, person_id)
                        if session_person is None:
                            logger.warning(
                                "Skipping identity event for missing SessionPerson: session=%s person=%s event=%s",
                                session_id,
                                person_id,
                                event_type,
                            )
                            await db.rollback()
                            return

                    if person_id and camera_id:
                        att_event = attendance.process_identity_event(
                            person_id, emp_id, state, camera_id, ts
                        )
                        if att_event:
                            from airco.models import AttendanceEvent

                            db.add(
                                AttendanceEvent(
                                    time=ts,
                                    session_id=session_id,
                                    tenant_id=settings.tenant_id,
                                    employee_id=emp_id,
                                    session_person_id=person_id,
                                    camera_id=camera_id,
                                    event_type="check_in",
                                )
                            )

                    if state == "unknown" and person_id:
                        alert = alerts.check_unknown_person(session_id, person_id, camera_id, ts)
                        if alert:
                            from airco.models import Alert

                            db.add(
                                Alert(
                                    **{k: v for k, v in alert.items() if k != "timestamp"},
                                    tenant_id=settings.tenant_id,
                                )
                            )
                            await publish_event(
                                StreamNames.ALERTS,
                                AlertEventPayload(
                                    session_id=session_id,
                                    session_person_id=person_id,
                                    alert_type="unknown_person",
                                    severity="medium",
                                    message=alert["message"],
                                    timestamp=ts,
                                ).to_redis(),
                            )

                elif stream == StreamNames.PHONES:
                    person_id = _parse_optional_uuid(fields.get("session_person_id"))
                    camera_id = _parse_optional_uuid(fields.get("camera_id"))
                    phone_track_id = _parse_optional_int(fields.get("track_id"))
                    if person_id is None and session_id and camera_id and phone_track_id is not None:
                        person_id = await _resolve_person_id(
                            db,
                            track_to_person=track_to_person,
                            session_id=session_id,
                            camera_id=camera_id,
                            track_id=phone_track_id,
                        )
                    if person_id:
                        session_person = await _load_session_person(db, person_id)
                        if session_person is None:
                            logger.warning(
                                "Skipping phone event for missing SessionPerson: session=%s person=%s",
                                session_id,
                                person_id,
                            )
                            await db.rollback()
                            return

                        dur = float(fields.get("duration_seconds", 0))
                        total = phones.add_phone_event(person_id, dur)
                        alert = alerts.check_phone_violation(session_id, person_id, camera_id, total, ts)
                        if alert:
                            from airco.models import Alert

                            db.add(
                                Alert(
                                    **{k: v for k, v in alert.items() if k != "timestamp"},
                                    tenant_id=settings.tenant_id,
                                )
                            )

                await db.commit()
            except Exception:
                await db.rollback()
                raise

        logger.info("Analytics consumer starting...")
        await consume_multiple_streams(
            streams=[StreamNames.TRACKS, StreamNames.IDENTITY, StreamNames.PHONES],
            group=GROUP,
            consumer=CONSUMER,
            handler=handler,
        )


if __name__ == "__main__":
    asyncio.run(main())
