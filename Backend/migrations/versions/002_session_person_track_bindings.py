"""Persistent session-person track bindings.

Revision ID: 002
Create Date: 2026-04-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_persons",
        sa.Column("merged_into_session_person_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_session_persons_merged_into_session_person_id",
        "session_persons",
        "session_persons",
        ["merged_into_session_person_id"],
        ["id"],
    )
    op.create_index(
        "ix_sp_merged_into",
        "session_persons",
        ["merged_into_session_person_id"],
    )

    op.create_table(
        "session_person_track_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("binding_state", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False, server_default=sa.text("'direct_track'")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("evidence_summary", JSONB),
    )
    op.create_index(
        "ix_spb_session_camera_track_state",
        "session_person_track_bindings",
        ["session_id", "camera_id", "track_id", "binding_state"],
    )
    op.create_index(
        "ix_spb_session_person",
        "session_person_track_bindings",
        ["session_person_id"],
    )
    op.create_index(
        "ix_spb_session_state_seen",
        "session_person_track_bindings",
        ["session_id", "binding_state", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_spb_session_state_seen", table_name="session_person_track_bindings")
    op.drop_index("ix_spb_session_person", table_name="session_person_track_bindings")
    op.drop_index("ix_spb_session_camera_track_state", table_name="session_person_track_bindings")
    op.drop_table("session_person_track_bindings")

    op.drop_index("ix_sp_merged_into", table_name="session_persons")
    op.drop_constraint(
        "fk_session_persons_merged_into_session_person_id",
        "session_persons",
        type_="foreignkey",
    )
    op.drop_column("session_persons", "merged_into_session_person_id")
