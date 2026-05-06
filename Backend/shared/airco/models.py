"""SQLAlchemy ORM models for Airco Secure 2.0.

Tables are organized into:
- Core tables (regular PostgreSQL): tenants, cameras, employees, sessions
- Canonical state (regular, mutable): session_persons, alerts, review_tasks
- Time-series (TimescaleDB hypertables): track_events, face_observations, etc.
- Embeddings (pgvector): employee_face_templates, person_embeddings
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Core Tables ──────────────────────────────────────────


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    zone: Mapped[str | None] = mapped_column(String(255))
    rtsp_url: Mapped[str] = mapped_column(Text)
    is_entrance: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    topology_config: Mapped[dict | None] = mapped_column(JSONB, default=None)
    face_accept_threshold: Mapped[float | None] = mapped_column(Float, default=None)
    face_uncertain_threshold: Mapped[float | None] = mapped_column(Float, default=None)
    body_accept_threshold: Mapped[float | None] = mapped_column(Float, default=None)
    body_uncertain_threshold: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CameraPair(Base):
    """Camera topology metadata for cross-camera identity scoring."""
    __tablename__ = "camera_pairs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    camera_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    overlap_type: Mapped[str] = mapped_column(
        Enum("overlapping", "adjacent", "distant", name="overlap_type_enum"),
        default="distant",
    )
    transition_min_sec: Mapped[float] = mapped_column(Float, default=0.0)
    transition_max_sec: Mapped[float] = mapped_column(Float, default=300.0)
    same_space: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_camera_pair_unique", "camera_a_id", "camera_b_id", unique=True),
    )


class CameraZone(Base):
    """Zone annotations for exit/entry gating in cross-camera tracking."""
    __tablename__ = "camera_zones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"), index=True)
    zone_type: Mapped[str] = mapped_column(String(20))  # entry, exit, door
    zone_name: Mapped[str | None] = mapped_column(String(100))
    bbox_x1: Mapped[float | None] = mapped_column(Float)
    bbox_y1: Mapped[float | None] = mapped_column(Float)
    bbox_x2: Mapped[float | None] = mapped_column(Float)
    bbox_y2: Mapped[float | None] = mapped_column(Float)
    connects_to_camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    employee_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    department: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")  # active, inactive
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeFaceTemplate(Base):
    """Face embeddings for enrolled employees. Multiple per employee (different angles)."""
    __tablename__ = "employee_face_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    embedding: Mapped[list] = mapped_column(Vector(512))  # ArcFace 512-d
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    angle_label: Mapped[str | None] = mapped_column(String(32))  # frontal, left, right, etc.
    source_camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id"), default=None)
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), default=None)
    capture_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sample_image_object_name: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeFaceTrainingJob(Base):
    __tablename__ = "employee_face_training_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="capturing")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    captured_frames: Mapped[int] = mapped_column(Integer, default=0)
    accepted_frames: Mapped[int] = mapped_column(Integer, default=0)
    rejected_frames: Mapped[int] = mapped_column(Integer, default=0)
    target_frames: Mapped[int] = mapped_column(Integer, default=100)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=120)
    replace_existing: Mapped[bool] = mapped_column(Boolean, default=False)
    debug_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    angle_coverage: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    export_object_name: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    detector_face_count: Mapped[int] = mapped_column(Integer, default=0)
    detector_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    detector_bbox: Mapped[list | None] = mapped_column(JSONB, default=None)
    rejection_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Session Tables ───────────────────────────────────────


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    profile: Mapped[str | None] = mapped_column(String(255))  # "Daily Surveillance", etc.
    mode: Mapped[str] = mapped_column(String(32), default="live")  # live, replay
    status: Mapped[str] = mapped_column(String(32), default="created")
    # Status: created → running → paused → running → stopped
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict | None] = mapped_column(JSONB, default=None)
    summary: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionCamera(Base):
    __tablename__ = "session_cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_file_path: Mapped[str | None] = mapped_column(Text)
    replay_offset_sec: Mapped[float] = mapped_column(Float, default=0.0)


# ── Canonical Person (Heart of 2.0) ─────────────────────


class SessionPerson(Base):
    """One record per physical person per session. THE source of truth."""
    __tablename__ = "session_persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    recognition_state: Mapped[str] = mapped_column(
        Enum("unknown", "candidate", "identified", "corrected", name="recognition_state_enum"),
        default="unknown",
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    display_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    current_cameras: Mapped[list | None] = mapped_column(ARRAY(String), default=list)
    active_track_bindings: Mapped[dict | None] = mapped_column(JSONB, default=list)
    merged_into_session_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_persons.id"),
    )
    identity_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_clusters.id"),
    )
    face_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    body_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    identity_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    best_thumbnail_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    face_template: Mapped[list | None] = mapped_column(Vector(512), default=None)
    body_template: Mapped[list | None] = mapped_column(Vector(512), default=None)
    face_template_updates: Mapped[int] = mapped_column(Integer, default=0)
    body_template_updates: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_sp_session_active", "session_id", "is_active"),
        Index("ix_sp_employee", "employee_id"),
        Index("ix_sp_merged_into", "merged_into_session_person_id"),
        Index("ix_sp_identity_cluster", "identity_cluster_id"),
    )


class IdentityCluster(Base):
    """Reusable cross-session anonymous or employee-linked identity bucket."""
    __tablename__ = "identity_clusters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    cluster_state: Mapped[str] = mapped_column(
        Enum("anonymous", "employee_linked", "superseded", name="identity_cluster_state_enum"),
        default="anonymous",
    )
    display_label: Mapped[str] = mapped_column(String(255), default="Unknown Identity Cluster")
    best_thumbnail_url: Mapped[str | None] = mapped_column(Text)
    face_template: Mapped[list | None] = mapped_column(Vector(512), default=None)
    body_template: Mapped[list | None] = mapped_column(Vector(512), default=None)
    face_template_updates: Mapped[int] = mapped_column(Integer, default=0)
    body_template_updates: Mapped[int] = mapped_column(Integer, default=0)
    evidence_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_by_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_clusters.id"),
    )

    __table_args__ = (
        Index("ix_ic_tenant_state", "tenant_id", "cluster_state"),
        Index("ix_ic_employee", "employee_id"),
    )


class IdentityClusterMember(Base):
    """Maps session-local canonical persons into reusable identity clusters."""
    __tablename__ = "identity_cluster_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity_clusters.id"), index=True)
    session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"), index=True)
    member_role: Mapped[str] = mapped_column(
        Enum("seed", "merged", "employee_assignment", name="identity_cluster_member_role_enum"),
        default="seed",
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_icm_cluster_active", "identity_cluster_id", "active"),
        Index("ix_icm_session_person_active", "session_person_id", "active"),
    )


class IdentityMergeReview(Base):
    """Immutable audit log for operator identity merge decisions."""
    __tablename__ = "identity_merge_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    review_type: Mapped[str] = mapped_column(
        Enum("unknown_merge", "assign_employee", "split_member", "undo_review", name="identity_review_type_enum"),
        default="unknown_merge",
    )
    decision: Mapped[str] = mapped_column(
        Enum("confirmed", "reverted", name="identity_review_decision_enum"),
        default="confirmed",
    )
    source_session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"), index=True)
    target_session_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("session_persons.id"))
    target_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    source_cluster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity_clusters.id"))
    target_cluster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity_clusters.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reverted_by: Mapped[str | None] = mapped_column(String(255))
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revert_review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity_merge_reviews.id"))

    __table_args__ = (
        Index("ix_imr_tenant_created", "tenant_id", "created_at"),
        Index("ix_imr_source_person", "source_session_person_id"),
    )


class SessionPersonTrackBinding(Base):
    """Persistent ownership of a track by a canonical session person."""
    __tablename__ = "session_person_track_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"), index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"), index=True)
    track_id: Mapped[int] = mapped_column(Integer)
    binding_state: Mapped[str] = mapped_column(String(16), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), default="direct_track")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_spb_session_camera_track_state", "session_id", "camera_id", "track_id", "binding_state"),
        Index("ix_spb_session_person", "session_person_id"),
        Index("ix_spb_session_state_seen", "session_id", "binding_state", "last_seen_at"),
    )


class PersonEmbedding(Base):
    """Face and body embeddings collected for a session person."""
    __tablename__ = "person_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"), index=True)
    embedding_type: Mapped[str] = mapped_column(String(16))  # "face" or "body"
    embedding: Mapped[list] = mapped_column(Vector(512))
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Time-Series Event Tables (TimescaleDB Hypertables) ───


class TrackEvent(Base):
    """Raw track observations from the GPU pipeline."""
    __tablename__ = "track_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    track_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))  # track_started, track_observed, track_ended
    bbox: Mapped[list | None] = mapped_column(JSONB)  # [x1, y1, x2, y2]
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    frame_number: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_te_session_camera", "session_id", "camera_id"),
    )


class FaceObservation(Base):
    """Face detections with embeddings and match results."""
    __tablename__ = "face_observations"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    track_id: Mapped[int] = mapped_column(Integer)
    session_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    embedding: Mapped[list | None] = mapped_column(Vector(512))
    top_matches: Mapped[dict | None] = mapped_column(JSONB)  # [{employee_id, score}, ...]
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_fo_person", "session_person_id"),
    )


class IdentityEvent(Base):
    """Identity state transitions."""
    __tablename__ = "identity_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(32))
    old_state: Mapped[str | None] = mapped_column(String(32))
    new_state: Mapped[str | None] = mapped_column(String(32))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence: Mapped[dict | None] = mapped_column(JSONB)


class PhoneEvent(Base):
    """Phone detection events."""
    __tablename__ = "phone_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    track_id: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class ActivityEvent(Base):
    """Activity classification events (working/idle/walking)."""
    __tablename__ = "activity_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    activity: Mapped[str] = mapped_column(String(32))  # working, idle, walking
    confidence: Mapped[float] = mapped_column(Float)


class AttendanceEvent(Base):
    """Attendance check-in/check-out events."""
    __tablename__ = "attendance_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(32))  # check_in, check_out
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


# ── Mutable State Tables ────────────────────────────────


class CameraPresenceSegment(Base):
    """Person presence per camera with start/end times. Updated on exit."""
    __tablename__ = "camera_presence_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"), index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dwell_seconds: Mapped[float | None] = mapped_column(Float)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("session_persons.id"))
    alert_type: Mapped[str] = mapped_column(String(64))
    # unknown_person, phone_violation, idle_alert, attendance, restricted_zone
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_url: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active, acknowledged, resolved
    dedup_key: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    session_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("session_persons.id"))
    camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(64))  # alert, identity, periodic
    full_frame_url: Mapped[str | None] = mapped_column(Text)
    face_crop_url: Mapped[str | None] = mapped_column(Text)
    body_crop_url: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    session_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_persons.id"))
    task_type: Mapped[str] = mapped_column(String(64))
    # unknown_review, conflict_review, false_positive_review
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, completed, skipped
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    decision: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_rt_status", "status"),
    )
