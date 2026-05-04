"""Pydantic event schemas — the contract between all services.

Every event published to Redis Streams uses these schemas.
to_redis() serializes for Redis (flat string dict).
from_redis() deserializes from Redis fields.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class StreamNames:
    """Redis Stream names. Single source of truth."""
    TRACKS = "airco:tracks"
    CROPS = "airco:crops"
    PHONES = "airco:phones"
    IDENTITY = "airco:identity"
    SNAPSHOTS = "airco:snapshots"
    ALERTS = "airco:alerts"
    OVERVIEW = "airco:overview"


LIVE_EVENT_VERSION = "1"
LIVE_EVENT_BINARY_FIELDS = frozenset({"crop_b64", "full_frame_b64", "embedding"})
LIVE_EVENT_META_FIELDS = frozenset(
    {"event_type", "tenant_id", "session_id", "timestamp", "occurred_at", "type", "version"}
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def normalize_live_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip transport-only fields and large binary blobs from live payloads."""
    return {
        key: _json_safe(value)
        for key, value in payload.items()
        if key not in LIVE_EVENT_META_FIELDS and key not in LIVE_EVENT_BINARY_FIELDS
    }


def build_live_event_envelope(
    *,
    event_type: str,
    tenant_id: str | None,
    session_id: str | None,
    occurred_at: datetime | str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the frontend live-event envelope used by websocket publishing."""
    if isinstance(occurred_at, datetime):
        occurred_at = occurred_at.isoformat()
    elif occurred_at is not None:
        occurred_at = str(occurred_at)

    return {
        "type": event_type,
        "version": LIVE_EVENT_VERSION,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "occurred_at": occurred_at,
        "payload": normalize_live_event_payload(payload),
    }


def resolve_live_event_channel(
    *, stream: str, event_type: str, tenant_id: str | None, session_id: str | None
) -> str:
    """Map a live event to the Centrifugo channel the frontend listens on."""
    normalized_stream = (stream or "").lower()
    normalized_event_type = (event_type or "").lower()
    if normalized_stream == StreamNames.OVERVIEW:
        return f"tenant:{tenant_id or 'unknown'}:overview"
    if normalized_stream == StreamNames.ALERTS or normalized_event_type.startswith("alert"):
        return f"alerts:{session_id or 'unknown'}"
    return f"sessions:{session_id or 'unknown'}"


class BaseEvent(BaseModel):
    """Base for all events. Provides Redis serialization."""

    event_type: str
    session_id: uuid.UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_redis(self) -> dict[str, str]:
        """Serialize to flat dict of strings for Redis XADD."""
        data = self.model_dump(mode="json")
        return {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}

    @classmethod
    def from_redis(cls, fields: dict[str, str]) -> "BaseEvent":
        """Deserialize from Redis XREAD fields."""
        parsed = {}
        for k, v in fields.items():
            try:
                parsed[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed[k] = v
        return cls.model_validate(parsed)


# ── Pipeline → Redis ─────────────────────────────────────


class TrackEventPayload(BaseEvent):
    """Emitted by savant-pipeline for every track lifecycle event."""
    camera_id: uuid.UUID
    track_id: int
    event_type: str  # track_started, track_observed, track_ended
    bbox: list[float] = Field(default_factory=list)  # [x1, y1, x2, y2]
    confidence: float = 0.0
    frame_number: int | None = None


class CropEventPayload(BaseEvent):
    """Emitted by savant-pipeline with face/body crops for identity processing."""
    camera_id: uuid.UUID
    track_id: int
    event_type: str  # face_crop, body_crop
    crop_b64: str  # base64-encoded JPEG
    bbox: list[float] = Field(default_factory=list)
    quality_score: float = 0.0


class PhoneEventPayload(BaseEvent):
    """Emitted by savant-pipeline when phone detected near a person."""
    camera_id: uuid.UUID
    track_id: int
    event_type: str = "phone_detected"
    confidence: float = 0.0
    duration_seconds: float = 0.0


# ── Identity Consumer → Redis ────────────────────────────


class IdentityEventPayload(BaseEvent):
    """Emitted by identity-consumer on state transitions."""
    session_person_id: uuid.UUID
    event_type: str  # person_created, person_updated, person_merged, person_identified, person_corrected
    recognition_state: str  # unknown, candidate, identified, corrected
    track_id: Optional[int] = None
    employee_id: Optional[uuid.UUID] = None
    employee_name: Optional[str] = None
    camera_id: Optional[uuid.UUID] = None
    confidence: float = 0.0


# ── Snapshot Events ──────────────────────────────────────


class SnapshotEventPayload(BaseEvent):
    """Request to capture and store a snapshot with evidence overlay."""
    camera_id: uuid.UUID
    track_id: int
    session_person_id: Optional[uuid.UUID] = None
    event_type: str = "snapshot_requested"
    trigger: str = "alert"  # alert, identity, periodic
    full_frame_b64: str = ""  # base64-encoded full frame JPEG
    bbox: list[float] = Field(default_factory=list)
    label: str = ""


# ── Alert Events ─────────────────────────────────────────


class AlertEventPayload(BaseEvent):
    """Emitted by analytics-consumer when an alert is generated."""
    alert_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    session_person_id: Optional[uuid.UUID] = None
    event_type: str = "alert_created"
    alert_type: str  # unknown_person, phone_violation, idle_alert, attendance, restricted_zone
    severity: str = "medium"  # low, medium, high, critical
    camera_id: Optional[uuid.UUID] = None
    message: str = ""
    evidence_url: Optional[str] = None
