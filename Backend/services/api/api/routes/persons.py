"""Session persons CRUD plus dashboard-facing unknown-person views."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from airco.db import get_session
from airco.minio_client import get_presigned_url
from airco.models import (
    Alert,
    Camera,
    CameraPresenceSegment,
    PersonEmbedding,
    PhoneEvent,
    Session,
    SessionPerson,
    SessionPersonTrackBinding,
    Snapshot,
)
from api.auth import AuthState, require_authenticated
from api.person_evidence import (
    build_unknown_person_evidence_payload,
    select_person_evidence_snapshots,
)

router = APIRouter()

LOW_FACE_CONFIDENCE_THRESHOLD = 0.5
ACTIVE_UNKNOWN_ALERT_TYPES = {"unknown_person", "phone_violation"}
UNKNOWN_VISITOR_LABEL = "Unknown Visitor"
# Minimum presence duration (seconds) for a person to appear on the manager
# dashboard.  Persons tracked for less than this are considered ghost detections
# (brief false positives, someone walking through the far edge of frame, etc.)
# and are hidden at query time.  All data is still captured and stored.
MINIMUM_PRESENCE_SECONDS = 5.0
PUBLIC_EVIDENCE_KIND_ALIASES = {
    "alert_moment": "alert_backed",
    "last_seen": "latest_seen",
}


class PersonResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    employee_id: uuid.UUID | None
    display_name: str
    recognition_state: str
    is_active: bool
    first_seen_at: str | None
    last_seen_at: str | None
    face_confidence: float | None
    model_config = {"from_attributes": True}


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_asset_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if "://" in normalized or normalized.startswith("data:") or normalized.startswith("blob:"):
        return normalized
    try:
        return get_presigned_url(normalized.lstrip("/"))
    except Exception:
        return normalized


def _session_run_start(session: Session) -> datetime | None:
    return getattr(session, "started_at", None) or getattr(session, "created_at", None)


def _session_run_end(session: Session) -> datetime | None:
    if getattr(session, "status", None) == "running":
        return None
    return getattr(session, "stopped_at", None)


def _within_session_run(value: datetime | None, *, session: Session) -> bool:
    if value is None:
        return False
    run_start = _session_run_start(session)
    run_end = _session_run_end(session)
    if run_start is not None and value < run_start:
        return False
    if run_end is not None and value > run_end:
        return False
    return True


def _person_is_unknown(person: SessionPerson) -> bool:
    return getattr(person, "employee_id", None) is None and getattr(person, "recognition_state", None) == "unknown"


def _person_display_name(person: SessionPerson) -> str:
    if _person_is_unknown(person):
        return _stable_unknown_display_name(person)
    return getattr(person, "display_name", "Unknown")


def _serialize_person_response(person: SessionPerson) -> dict:
    return {
        "id": str(person.id),
        "session_id": str(person.session_id),
        "employee_id": str(person.employee_id) if getattr(person, "employee_id", None) else None,
        "display_name": _person_display_name(person),
        "recognition_state": getattr(person, "recognition_state", "unknown"),
        "is_active": bool(getattr(person, "is_active", False)),
        "first_seen_at": _utc_iso(getattr(person, "first_seen_at", None)),
        "last_seen_at": _utc_iso(getattr(person, "last_seen_at", None)),
        "face_confidence": getattr(person, "face_confidence", None),
    }


def _all_items(result) -> list:
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "scalar_one_or_none") and not hasattr(result, "all"):
        item = result.scalar_one_or_none()
        return [] if item is None else [item]
    if hasattr(result, "all"):
        return result.all()
    return list(result)


def _selected_camera_context(person: SessionPerson, cameras_by_id: dict[str, Camera]) -> tuple[str | None, str | None]:
    camera_ids = sorted({str(camera_id) for camera_id in (getattr(person, "current_cameras", None) or [])})
    for camera_id in camera_ids:
        camera = cameras_by_id.get(camera_id)
        if camera is not None:
            return camera.name, camera.zone
    return None, None


def _alert_summary(alerts: list[Alert]) -> tuple[int, list[str]]:
    active_alerts = [alert for alert in alerts if getattr(alert, "status", "active") == "active"]
    alert_types = sorted({getattr(alert, "alert_type", "") for alert in active_alerts if getattr(alert, "alert_type", None)})
    return len(active_alerts), alert_types


def _dwell_summary(segments: list[CameraPresenceSegment]) -> tuple[float, list[dict]]:
    by_camera: dict[str, dict] = {}
    total_seconds = 0.0
    for segment in segments:
        camera_key = str(segment.camera_id)
        entry = by_camera.get(camera_key)
        if entry is None:
            entry = {
                "camera_id": camera_key,
                "camera_name": None,
                "zone": None,
                "dwell_seconds": 0.0,
            }
            by_camera[camera_key] = entry
        dwell_seconds = float(getattr(segment, "dwell_seconds", 0.0) or 0.0)
        entry["dwell_seconds"] += dwell_seconds
        total_seconds += dwell_seconds
    return total_seconds, list(by_camera.values())


def _risk_context(
    *,
    person: SessionPerson,
    active_alert_types: list[str],
    dwell_seconds: float,
) -> tuple[str, int, list[str], str]:
    risk_reasons = ["unknown_identity"]
    risk_score = 60

    if "unknown_person" in active_alert_types:
        risk_reasons.append("active_unknown_person_alert")
        risk_score += 20

    face_confidence = float(getattr(person, "face_confidence", 0.0) or 0.0)
    if face_confidence < LOW_FACE_CONFIDENCE_THRESHOLD:
        risk_reasons.append("low_face_confidence")
        risk_score += 10

    if getattr(person, "identity_conflict", False):
        risk_reasons.append("identity_conflict")
        risk_score += 10

    if dwell_seconds >= 300.0:
        risk_reasons.append("extended_dwell")
        risk_score += 5

    risk_score = min(risk_score, 100)
    if risk_score >= 70:
        risk_level = "high"
    elif risk_score >= 40:
        risk_level = "medium"
    else:
        risk_level = "low"

    return risk_level, risk_score, risk_reasons, "review_identity_evidence"


def _stable_unknown_display_name(person: SessionPerson) -> str:
    person_id = getattr(person, "id", None)
    if isinstance(person_id, uuid.UUID):
        short_id = person_id.hex[:8].upper()
    else:
        short_id = str(person_id or "unknown")[:8].upper()
    return f"Unknown Person {short_id}"


def _unknown_person_display_names(persons: list[SessionPerson]) -> dict[str, str]:
    fallback_time = datetime.max.replace(tzinfo=timezone.utc)
    ordered_people = sorted(
        [person for person in persons if _person_is_unknown(person)],
        key=lambda person: (
            getattr(person, "first_seen_at", None) or fallback_time,
            getattr(person, "last_seen_at", None) or fallback_time,
            str(getattr(person, "id", "")),
        ),
    )
    return {
        str(person.id): f"{UNKNOWN_VISITOR_LABEL} {index:02d}"
        for index, person in enumerate(ordered_people, start=1)
    }


def _public_evidence_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    return PUBLIC_EVIDENCE_KIND_ALIASES.get(kind, kind)


def _serialize_evidence_moment(moment: dict) -> dict:
    payload = {
        "id": str(moment.get("id", "")),
        "kind": _public_evidence_kind(moment.get("kind")),
        "occurred_at": _utc_iso(moment.get("occurred_at")),
        "camera_id": moment.get("camera_id"),
        "camera_name": moment.get("camera_name"),
        "zone": moment.get("zone"),
        "image_url": _public_asset_url(moment.get("image_url")),
        "thumbnail_url": _public_asset_url(moment.get("thumbnail_url")),
        "selection_reason": moment.get("selection_reason"),
    }
    for key in ("to_camera_name", "to_zone", "alert_type", "segment_id"):
        if moment.get(key) is not None:
            payload[key] = moment.get(key)
    return payload


def _serialize_evidence_payload(evidence_payload: dict) -> dict:
    timeline = evidence_payload["timeline"]
    return {
        "timeline": {
            "window_start": _utc_iso(timeline.get("window_start")),
            "window_end": _utc_iso(timeline.get("window_end")),
            "moments": [_serialize_evidence_moment(moment) for moment in timeline.get("moments", [])],
        },
        "storyboard": [_serialize_evidence_moment(moment) for moment in evidence_payload.get("storyboard", [])],
        "continuity_confidence": evidence_payload.get("continuity_confidence"),
        "continuity_reasons": evidence_payload.get("continuity_reasons", []),
    }


def _list_person_payload(
    *,
    person: SessionPerson,
    cameras_by_id: dict[str, Camera],
    alerts_by_person_id: dict[str, list[Alert]],
    dwell_by_person_id: dict[str, float],
) -> dict:
    camera_name, zone_name = _selected_camera_context(person, cameras_by_id)
    active_alerts = alerts_by_person_id.get(str(person.id), [])
    active_alert_count, active_alert_types = _alert_summary(active_alerts)
    dwell_seconds = float(dwell_by_person_id.get(str(person.id), 0.0))
    _risk_level, _, _risk_reasons, _ = _risk_context(
        person=person,
        active_alert_types=active_alert_types,
        dwell_seconds=dwell_seconds,
    )

    return {
        "id": str(person.id),
        "session_id": str(person.session_id),
        "display_name": _person_display_name(person),
        "recognition_state": getattr(person, "recognition_state", "unknown"),
        "is_active": bool(getattr(person, "is_active", False)),
        "first_seen_at": _utc_iso(getattr(person, "first_seen_at", None)),
        "last_seen_at": _utc_iso(getattr(person, "last_seen_at", None)),
        "best_thumbnail_url": _public_asset_url(getattr(person, "best_thumbnail_url", None)),
        "current_camera_id": str(next(iter(sorted({str(camera_id) for camera_id in (getattr(person, "current_cameras", None) or [])})), "")) or None,
        "current_camera_name": camera_name,
        "current_zone": zone_name,
        "face_confidence": float(getattr(person, "face_confidence", 0.0) or 0.0),
        "total_dwell_seconds": round(dwell_seconds, 1),
        "active_alerts": [
            {
                "id": str(alert.id),
                "type": getattr(alert, "alert_type", None),
                "severity": getattr(alert, "severity", None),
                "status": getattr(alert, "status", None),
                "created_at": _utc_iso(getattr(alert, "created_at", None)),
            }
            for alert in active_alerts
        ],
        "alert_count": active_alert_count,
    }


def _unknown_person_card(
    *,
    person: SessionPerson,
    cameras_by_id: dict[str, Camera],
    alerts: list[Alert],
    dwell_segments: list[CameraPresenceSegment],
    display_name: str | None = None,
) -> dict:
    camera_name, zone_name = _selected_camera_context(person, cameras_by_id)
    total_dwell_seconds, _ = _dwell_summary(dwell_segments)
    active_alert_count, active_alert_types = _alert_summary(alerts)
    _risk_level, _, risk_reasons, _ = _risk_context(
        person=person,
        active_alert_types=active_alert_types,
        dwell_seconds=total_dwell_seconds,
    )

    return {
        "person_id": str(person.id),
        "display_name": display_name or _person_display_name(person),
        "recognition_state": getattr(person, "recognition_state", "unknown"),
        "is_active": bool(getattr(person, "is_active", False)),
        "first_seen_at": _utc_iso(getattr(person, "first_seen_at", None)),
        "last_seen_at": _utc_iso(getattr(person, "last_seen_at", None)),
        "current_camera": camera_name,
        "current_zone": zone_name,
        "face_confidence": float(getattr(person, "face_confidence", 0.0) or 0.0),
        "body_confidence": float(getattr(person, "body_confidence", 0.0) or 0.0),
        "dwell_seconds": round(total_dwell_seconds, 1),
        "active_alert_count": active_alert_count,
        "active_alert_types": active_alert_types,
        "risk_level": _risk_level,
        "risk_reasons": risk_reasons,
        "best_thumbnail_url": _public_asset_url(getattr(person, "best_thumbnail_url", None)),
        "evidence_summary": getattr(person, "evidence_summary", None) or {},
    }


def _detail_payload(
    *,
    person: SessionPerson,
    cameras_by_id: dict[str, Camera],
    alerts: list[Alert],
    dwell_segments: list[CameraPresenceSegment],
    phone_events: list[PhoneEvent],
) -> dict:
    total_dwell_seconds, dwell_entries = _dwell_summary(dwell_segments)
    for entry in dwell_entries:
        camera = cameras_by_id.get(entry["camera_id"])
        if camera is not None:
            entry["camera_name"] = getattr(camera, "name", None)
            entry["zone"] = getattr(camera, "zone", None)
    active_alert_count, active_alert_types = _alert_summary(alerts)
    phone_usage_seconds = sum(float(getattr(event, "duration_seconds", 0.0) or 0.0) for event in phone_events)
    risk_level, risk_score, risk_reasons, recommended_action = _risk_context(
        person=person,
        active_alert_types=active_alert_types,
        dwell_seconds=total_dwell_seconds,
    )

    return {
        "dwell_by_camera": dwell_entries,
        "alert_count": active_alert_count,
        "phone_usage_minutes": round(phone_usage_seconds / 60.0, 1),
        "risk_context": {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "risk_factors": risk_reasons,
            "recommended_action": recommended_action,
        },
    }


async def _load_unknown_person_context(
    *,
    session_id: uuid.UUID,
    auth: AuthState,
    db: AsyncSession,
    person_id: uuid.UUID | None = None,
    include_phone_events: bool = True,
) -> tuple[Session, dict[str, Camera], list[SessionPerson], dict[str, list[Alert]], dict[str, list[CameraPresenceSegment]], dict[str, list[PhoneEvent]]]:
    session_result = await db.execute(
        select(Session).where(Session.id == session_id, Session.tenant_id == auth.tenant_id)
    )
    session_items = _all_items(session_result)
    session = session_items[0] if session_items else None
    if session is None:
        raise HTTPException(404, "Session not found")

    cameras_result = await db.execute(select(Camera).where(Camera.tenant_id == auth.tenant_id))
    cameras = {str(camera.id): camera for camera in _all_items(cameras_result)}

    person_query = select(SessionPerson).where(
        SessionPerson.session_id == session_id,
        SessionPerson.recognition_state == "unknown",
        # Never show persons that were merged into another canonical person.
        SessionPerson.merged_into_session_person_id.is_(None),
    )
    if person_id is not None:
        person_query = person_query.where(SessionPerson.id == person_id)
    person_query = person_query.order_by(SessionPerson.last_seen_at.desc(), SessionPerson.first_seen_at.desc())
    persons_result = await db.execute(person_query)
    all_persons = [
        person
        for person in _all_items(persons_result)
        if _within_session_run(getattr(person, "last_seen_at", None), session=session)
    ]

    # Filter ghost detections: persons tracked for less than MINIMUM_PRESENCE_SECONDS
    # are hidden from the dashboard (data is still captured for re-ID purposes).
    persons = []
    for person in all_persons:
        first = getattr(person, "first_seen_at", None)
        last = getattr(person, "last_seen_at", None)
        if first and last:
            presence = (last - first).total_seconds()
            if presence < MINIMUM_PRESENCE_SECONDS:
                continue
        persons.append(person)

    if person_id is not None and not persons:
        raise HTTPException(404, "Unknown person not found")

    person_ids = [person.id for person in persons]

    alerts_by_person_id: dict[str, list[Alert]] = {}
    if person_ids:
        alerts_result = await db.execute(
            select(Alert)
            .where(
                Alert.session_id == session_id,
                Alert.session_person_id.in_(person_ids),
            )
            .order_by(Alert.created_at.asc())
        )
        for alert in _all_items(alerts_result):
            if not _within_session_run(getattr(alert, "created_at", None), session=session):
                continue
            if getattr(alert, "status", "active") != "active":
                continue
            if getattr(alert, "alert_type", None) not in ACTIVE_UNKNOWN_ALERT_TYPES and getattr(
                alert, "alert_type", None
            ) != "unknown_person":
                continue
            alerts_by_person_id.setdefault(str(alert.session_person_id), []).append(alert)

    dwell_by_person_id: dict[str, list[CameraPresenceSegment]] = {}
    if person_ids:
        dwell_result = await db.execute(
            select(CameraPresenceSegment)
            .where(
                CameraPresenceSegment.session_id == session_id,
                CameraPresenceSegment.session_person_id.in_(person_ids),
            )
            .order_by(CameraPresenceSegment.entered_at.asc())
        )
        for segment in _all_items(dwell_result):
            segment_time = getattr(segment, "entered_at", None) or getattr(segment, "exited_at", None)
            if not _within_session_run(segment_time, session=session):
                continue
            dwell_by_person_id.setdefault(str(segment.session_person_id), []).append(segment)

    phone_by_person_id: dict[str, list[PhoneEvent]] = {}
    if include_phone_events and person_ids:
        phone_result = await db.execute(
            select(PhoneEvent)
            .where(
                PhoneEvent.session_id == session_id,
                PhoneEvent.session_person_id.in_(person_ids),
            )
            .order_by(PhoneEvent.time.asc())
        )
        for event in _all_items(phone_result):
            if not _within_session_run(getattr(event, "time", None), session=session):
                continue
            phone_by_person_id.setdefault(str(event.session_person_id), []).append(event)

    return session, cameras, persons, alerts_by_person_id, dwell_by_person_id, phone_by_person_id


@router.get("/unknown", dependencies=[Depends(require_authenticated)])
async def list_unknown_persons(
    session_id: uuid.UUID = Query(...),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    session, cameras_by_id, persons, alerts_by_person_id, dwell_by_person_id, _ = await _load_unknown_person_context(
        session_id=session_id,
        auth=auth,
        db=db,
        include_phone_events=False,
    )
    display_names_by_person_id = _unknown_person_display_names(persons)

    cards = [
        _unknown_person_card(
            person=person,
            cameras_by_id=cameras_by_id,
            alerts=alerts_by_person_id.get(str(person.id), []),
            dwell_segments=dwell_by_person_id.get(str(person.id), []),
            display_name=display_names_by_person_id.get(str(person.id)),
        )
        for person in persons
        if _person_is_unknown(person)
    ]

    active_unknown_persons = sum(1 for card in cards if card["is_active"])
    active_alerts = sum(int(card["active_alert_count"]) for card in cards)
    high_risk_persons = sum(1 for card in cards if card["risk_level"] == "high")
    average_face_confidence = round(
        (
            sum(float(card["face_confidence"]) for card in cards) / len(cards)
            if cards
            else 0.0
        ),
        2,
    )

    return {
        "session_id": str(session.id),
        "summary": {
            "unknown_persons": len(cards),
            "active_unknown_persons": active_unknown_persons,
            "active_alerts": active_alerts,
            "high_risk_persons": high_risk_persons,
            "average_face_confidence": average_face_confidence,
        },
        "persons": cards,
    }


@router.get("/unknown/{person_id}", dependencies=[Depends(require_authenticated)])
async def get_unknown_person(
    person_id: uuid.UUID,
    session_id: uuid.UUID = Query(...),
    auth: AuthState = Depends(require_authenticated),
    db: AsyncSession = Depends(get_session),
):
    session, cameras_by_id, persons, alerts_by_person_id, dwell_by_person_id, phone_by_person_id = await _load_unknown_person_context(
        session_id=session_id,
        auth=auth,
        db=db,
        person_id=None,
    )
    person = next((candidate for candidate in persons if candidate.id == person_id), None)
    if person is None:
        raise HTTPException(404, "Unknown person not found")
    alerts = alerts_by_person_id.get(str(person.id), [])
    dwell_segments = dwell_by_person_id.get(str(person.id), [])
    phone_events = phone_by_person_id.get(str(person.id), [])
    display_names_by_person_id = _unknown_person_display_names(persons)
    bindings_result = await db.execute(
        select(SessionPersonTrackBinding)
        .where(
            SessionPersonTrackBinding.session_id == session_id,
            SessionPersonTrackBinding.session_person_id == person_id,
        )
        .order_by(SessionPersonTrackBinding.started_at.asc())
    )
    bindings = _all_items(bindings_result)
    candidate_camera_ids = {
        str(camera_id)
        for camera_id in (getattr(person, "current_cameras", None) or [])
        if str(camera_id)
    }
    candidate_camera_ids.update(
        str(segment.camera_id)
        for segment in dwell_segments
        if getattr(segment, "camera_id", None) is not None
    )
    candidate_camera_ids.update(
        str(binding.camera_id)
        for binding in bindings
        if getattr(binding, "camera_id", None) is not None
    )
    window_start = (getattr(person, "first_seen_at", None) or datetime.now(timezone.utc)) - timedelta(minutes=2)
    window_end = (getattr(person, "last_seen_at", None) or datetime.now(timezone.utc)) + timedelta(minutes=2)
    snapshots_stmt = (
        select(Snapshot)
        .where(
            Snapshot.session_id == session_id,
            Snapshot.created_at >= window_start,
            Snapshot.created_at <= window_end,
        )
        .order_by(Snapshot.created_at.asc())
    )
    if candidate_camera_ids:
        snapshots_stmt = snapshots_stmt.where(Snapshot.camera_id.in_(list(candidate_camera_ids)))
    snapshots_result = await db.execute(
        snapshots_stmt
    )
    snapshots = select_person_evidence_snapshots(
        person=person,
        snapshots=_all_items(snapshots_result),
        bindings=bindings,
        dwell_segments=dwell_segments,
    )

    payload = _detail_payload(
        person=person,
        cameras_by_id=cameras_by_id,
        alerts=alerts,
        dwell_segments=dwell_segments,
        phone_events=phone_events,
    )
    evidence_payload = build_unknown_person_evidence_payload(
        person=person,
        cameras_by_id=cameras_by_id,
        alerts=alerts,
        dwell_segments=dwell_segments,
        snapshots=snapshots,
    )
    serialized_evidence_payload = _serialize_evidence_payload(evidence_payload)
    person_card = _unknown_person_card(
        person=person,
        cameras_by_id=cameras_by_id,
        alerts=alerts,
        dwell_segments=dwell_segments,
        display_name=display_names_by_person_id.get(str(person.id)),
    )
    low_face_confidence = float(getattr(person, "face_confidence", 0.0) or 0.0) < LOW_FACE_CONFIDENCE_THRESHOLD

    return {
        "session_id": str(session.id),
        "person": {
            **person_card,
            "phone_usage_minutes": payload["phone_usage_minutes"],
            "continuity_confidence": serialized_evidence_payload["continuity_confidence"],
            "continuity_reasons": serialized_evidence_payload["continuity_reasons"],
        },
        "timeline": serialized_evidence_payload["timeline"],
        "storyboard": serialized_evidence_payload["storyboard"],
        "dwell_analysis": {
            "total_seconds": round(sum(entry["dwell_seconds"] for entry in payload["dwell_by_camera"]), 1),
            "by_camera": payload["dwell_by_camera"],
        },
        "violations": {
            "active_alert_count": person_card["active_alert_count"],
            "active_alert_types": person_card["active_alert_types"],
            "phone_usage_minutes": payload["phone_usage_minutes"],
            "identity_conflict": bool(getattr(person, "identity_conflict", False)),
            "low_face_confidence": low_face_confidence,
        },
        "risk_context": payload["risk_context"],
    }


@router.get("", response_model=list[PersonResponse], dependencies=[Depends(require_authenticated)])
async def list_persons(
    session_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(SessionPerson).where(
            SessionPerson.session_id == session_id,
            SessionPerson.merged_into_session_person_id.is_(None),
        )
    )
    persons = []
    for person in result.scalars().all():
        first = getattr(person, "first_seen_at", None)
        last = getattr(person, "last_seen_at", None)
        if first and last and (last - first).total_seconds() < MINIMUM_PRESENCE_SECONDS:
            continue
        persons.append(person)
    return [_serialize_person_response(person) for person in persons]


@router.get("/{person_id}", response_model=PersonResponse, dependencies=[Depends(require_authenticated)])
async def get_person(person_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(SessionPerson).where(SessionPerson.id == person_id))
    person = result.scalar_one_or_none()
    if not person:
        raise HTTPException(404, "Person not found")
    return _serialize_person_response(person)
