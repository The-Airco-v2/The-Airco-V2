"""Deep research identity pipeline — per-camera thresholds, template provenance,
EMA templates, camera zones.

Revision ID: 003
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Camera: per-camera threshold overrides ──
    op.add_column("cameras", sa.Column("face_accept_threshold", sa.Float(), nullable=True))
    op.add_column("cameras", sa.Column("face_uncertain_threshold", sa.Float(), nullable=True))
    op.add_column("cameras", sa.Column("body_accept_threshold", sa.Float(), nullable=True))
    op.add_column("cameras", sa.Column("body_uncertain_threshold", sa.Float(), nullable=True))

    # ── EmployeeFaceTemplate: provenance columns ──
    op.add_column("employee_face_templates", sa.Column("source_camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=True))
    op.add_column("employee_face_templates", sa.Column("source_session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=True))
    op.add_column("employee_face_templates", sa.Column("capture_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("employee_face_templates", sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("employee_face_templates", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False))

    # ── SessionPerson: EMA template columns ──
    op.add_column("session_persons", sa.Column("face_template", Vector(512), nullable=True))
    op.add_column("session_persons", sa.Column("body_template", Vector(512), nullable=True))
    op.add_column("session_persons", sa.Column("face_template_updates", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("session_persons", sa.Column("body_template_updates", sa.Integer(), server_default=sa.text("0"), nullable=False))

    # ── CameraZone: zone-based gating ──
    op.create_table(
        "camera_zones",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("zone_type", sa.String(32), nullable=False),
        sa.Column("zone_name", sa.String(128), nullable=True),
        sa.Column("bbox_x1", sa.Float(), nullable=False),
        sa.Column("bbox_y1", sa.Float(), nullable=False),
        sa.Column("bbox_x2", sa.Float(), nullable=False),
        sa.Column("bbox_y2", sa.Float(), nullable=False),
        sa.Column("connects_to_camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_camera_zones_camera_id", "camera_zones", ["camera_id"])


def downgrade() -> None:
    op.drop_index("ix_camera_zones_camera_id", table_name="camera_zones")
    op.drop_table("camera_zones")

    op.drop_column("session_persons", "body_template_updates")
    op.drop_column("session_persons", "face_template_updates")
    op.drop_column("session_persons", "body_template")
    op.drop_column("session_persons", "face_template")

    op.drop_column("employee_face_templates", "version")
    op.drop_column("employee_face_templates", "is_active")
    op.drop_column("employee_face_templates", "capture_date")
    op.drop_column("employee_face_templates", "source_session_id")
    op.drop_column("employee_face_templates", "source_camera_id")

    op.drop_column("cameras", "body_uncertain_threshold")
    op.drop_column("cameras", "body_accept_threshold")
    op.drop_column("cameras", "face_uncertain_threshold")
    op.drop_column("cameras", "face_accept_threshold")
