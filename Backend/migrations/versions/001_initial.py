"""Initial schema with TimescaleDB hypertables and pgvector.

Revision ID: 001
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, ENUM as PgENUM

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard: skip entire migration if enum types already exist (idempotent re-run)
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'overlap_type_enum'"
    )).scalar()
    if exists:
        return

    # ── Extensions ──
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── Enum types ──
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE overlap_type_enum AS ENUM ('overlapping', 'adjacent', 'distant');
        EXCEPTION WHEN duplicate_object THEN null; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE recognition_state_enum AS ENUM ('unknown', 'candidate', 'identified', 'corrected');
        EXCEPTION WHEN duplicate_object THEN null; END $$
    """)

    # ── Core tables ──
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("config", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "cameras",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("zone", sa.String(255)),
        sa.Column("rtsp_url", sa.Text, nullable=False),
        sa.Column("is_entrance", sa.Boolean, default=False),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("topology_config", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cameras_tenant", "cameras", ["tenant_id"])

    op.create_table(
        "camera_pairs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("camera_a_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("camera_b_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("overlap_type", PgENUM("overlapping", "adjacent", "distant", name="overlap_type_enum", create_type=False), default="distant"),
        sa.Column("transition_min_sec", sa.Float, default=0.0),
        sa.Column("transition_max_sec", sa.Float, default=300.0),
        sa.Column("same_space", sa.Boolean, default=False),
    )
    op.create_index("ix_camera_pair_unique", "camera_pairs", ["camera_a_id", "camera_b_id"], unique=True)

    op.create_table(
        "employees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("employee_code", sa.String(64), unique=True),
        sa.Column("department", sa.String(255)),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_employees_tenant", "employees", ["tenant_id"])

    op.execute("""
        CREATE TABLE employee_face_templates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            employee_id UUID NOT NULL REFERENCES employees(id),
            embedding vector(512) NOT NULL,
            quality_score FLOAT DEFAULT 0.0,
            angle_label VARCHAR(32),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.create_index("ix_eft_employee", "employee_face_templates", ["employee_id"])

    # ── Session tables ──
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("profile", sa.String(255)),
        sa.Column("mode", sa.String(32), default="live"),
        sa.Column("status", sa.String(32), default="created"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("config", JSONB),
        sa.Column("summary", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sessions_tenant", "sessions", ["tenant_id"])

    op.create_table(
        "session_cameras",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("is_replay", sa.Boolean, default=False),
        sa.Column("replay_file_path", sa.Text),
        sa.Column("replay_offset_sec", sa.Float, default=0.0),
    )
    op.create_index("ix_sc_session", "session_cameras", ["session_id"])

    # ── Canonical person ──
    op.create_table(
        "session_persons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("recognition_state", PgENUM("unknown", "candidate", "identified", "corrected", name="recognition_state_enum", create_type=False), default="unknown"),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id")),
        sa.Column("display_name", sa.String(255), default="Unknown"),
        sa.Column("current_cameras", ARRAY(sa.String)),
        sa.Column("active_track_bindings", JSONB),
        sa.Column("face_confidence", sa.Float, default=0.0),
        sa.Column("body_confidence", sa.Float, default=0.0),
        sa.Column("identity_conflict", sa.Boolean, default=False),
        sa.Column("best_thumbnail_url", sa.Text),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("evidence_summary", JSONB),
    )
    op.create_index("ix_sp_session_active", "session_persons", ["session_id", "is_active"])
    op.create_index("ix_sp_employee", "session_persons", ["employee_id"])

    op.execute("""
        CREATE TABLE person_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_person_id UUID NOT NULL REFERENCES session_persons(id),
            embedding_type VARCHAR(16) NOT NULL,
            embedding vector(512) NOT NULL,
            quality_score FLOAT DEFAULT 0.0,
            camera_id UUID REFERENCES cameras(id),
            captured_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.create_index("ix_pe_person", "person_embeddings", ["session_person_id"])

    # ── Time-series tables (converted to hypertables below) ──
    op.execute("""
        CREATE TABLE track_events (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            camera_id UUID NOT NULL,
            track_id INTEGER NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            bbox JSONB,
            confidence FLOAT DEFAULT 0.0,
            frame_number INTEGER
        )
    """)

    op.execute("""
        CREATE TABLE face_observations (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            camera_id UUID NOT NULL,
            track_id INTEGER NOT NULL,
            session_person_id UUID,
            embedding vector(512),
            top_matches JSONB,
            quality_score FLOAT DEFAULT 0.0
        )
    """)

    op.execute("""
        CREATE TABLE identity_events (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            session_person_id UUID NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            old_state VARCHAR(32),
            new_state VARCHAR(32),
            employee_id UUID,
            evidence JSONB
        )
    """)

    op.execute("""
        CREATE TABLE phone_events (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            session_person_id UUID,
            camera_id UUID NOT NULL,
            track_id INTEGER NOT NULL,
            confidence FLOAT NOT NULL,
            duration_seconds FLOAT DEFAULT 0.0
        )
    """)

    op.execute("""
        CREATE TABLE activity_events (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            session_person_id UUID,
            camera_id UUID NOT NULL,
            activity VARCHAR(32) NOT NULL,
            confidence FLOAT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE attendance_events (
            time TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            tenant_id VARCHAR(64) NOT NULL,
            employee_id UUID,
            session_person_id UUID NOT NULL,
            camera_id UUID NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            confidence FLOAT DEFAULT 0.0
        )
    """)

    # ── Convert to TimescaleDB hypertables ──
    op.execute("SELECT create_hypertable('track_events', 'time')")
    op.execute("SELECT create_hypertable('face_observations', 'time')")
    op.execute("SELECT create_hypertable('identity_events', 'time')")
    op.execute("SELECT create_hypertable('phone_events', 'time')")
    op.execute("SELECT create_hypertable('activity_events', 'time')")
    op.execute("SELECT create_hypertable('attendance_events', 'time')")

    # ── Hypertable indexes ──
    op.execute("CREATE INDEX ix_te_session_camera ON track_events (session_id, camera_id, time DESC)")
    op.execute("CREATE INDEX ix_fo_person ON face_observations (session_person_id, time DESC)")
    op.execute("CREATE INDEX ix_ie_person ON identity_events (session_person_id, time DESC)")
    op.execute("CREATE INDEX ix_pe_session ON phone_events (session_id, time DESC)")
    op.execute("CREATE INDEX ix_ae_session ON activity_events (session_id, time DESC)")
    op.execute("CREATE INDEX ix_att_session ON attendance_events (session_id, time DESC)")

    # ── Mutable state tables ──
    op.create_table(
        "camera_presence_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True)),
        sa.Column("dwell_seconds", sa.Float),
    )
    op.create_index("ix_cps_session", "camera_presence_segments", ["session_id"])
    op.create_index("ix_cps_person", "camera_presence_segments", ["session_person_id"])

    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id")),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), default="medium"),
        sa.Column("camera_id", UUID(as_uuid=True)),
        sa.Column("evidence_url", sa.Text),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("dedup_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alerts_session", "alerts", ["session_id"])
    op.create_index("ix_alerts_dedup", "alerts", ["dedup_key"])

    op.create_table(
        "snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id")),
        sa.Column("camera_id", UUID(as_uuid=True)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("full_frame_url", sa.Text),
        sa.Column("face_crop_url", sa.Text),
        sa.Column("body_crop_url", sa.Text),
        sa.Column("bbox", JSONB),
        sa.Column("score", sa.Float, default=0.0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_snapshots_session", "snapshots", ["session_id"])

    op.create_table(
        "review_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), default="pending"),
        sa.Column("evidence", JSONB),
        sa.Column("decision", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_rt_status", "review_tasks", ["status"])

    # ── TimescaleDB retention policies ──
    op.execute("SELECT add_retention_policy('track_events', INTERVAL '30 days')")
    op.execute("SELECT add_retention_policy('face_observations', INTERVAL '90 days')")
    op.execute("SELECT add_retention_policy('phone_events', INTERVAL '90 days')")
    op.execute("SELECT add_retention_policy('activity_events', INTERVAL '90 days')")

    # ── TimescaleDB compression policies ──
    op.execute("""
        ALTER TABLE track_events SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'session_id,camera_id'
        )
    """)
    op.execute("SELECT add_compression_policy('track_events', INTERVAL '7 days')")

    # ── Continuous aggregates for dashboard queries ──
    op.execute("""
        CREATE MATERIALIZED VIEW hourly_camera_stats
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', time) AS bucket,
            session_id,
            camera_id,
            COUNT(*) AS total_detections,
            COUNT(DISTINCT track_id) AS unique_tracks
        FROM track_events
        WHERE event_type = 'track_observed'
        GROUP BY bucket, session_id, camera_id
        WITH NO DATA
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('hourly_camera_stats',
            start_offset => INTERVAL '3 hours',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour')
    """)

    # ── Seed default tenant ──
    op.execute("INSERT INTO tenants (id, name) VALUES ('default', 'Default Tenant') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS hourly_camera_stats CASCADE")
    tables = [
        "review_tasks", "snapshots", "alerts", "camera_presence_segments",
        "attendance_events", "activity_events", "phone_events",
        "identity_events", "face_observations", "track_events",
        "person_embeddings", "session_persons", "session_cameras", "sessions",
        "employee_face_templates", "employees", "camera_pairs", "cameras", "tenants",
    ]
    for t in tables:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP TYPE IF EXISTS overlap_type_enum")
    op.execute("DROP TYPE IF EXISTS recognition_state_enum")
