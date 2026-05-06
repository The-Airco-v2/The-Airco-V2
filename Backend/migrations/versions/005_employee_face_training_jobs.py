"""Employee face training jobs and sample image pointers.

Revision ID: 005
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee_face_templates",
        sa.Column("sample_image_object_name", sa.Text(), nullable=True),
    )

    op.create_table(
        "employee_face_training_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'capturing'")),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("captured_frames", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted_frames", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rejected_frames", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("target_frames", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default=sa.text("120")),
        sa.Column("replace_existing", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("angle_coverage", JSONB, nullable=True),
        sa.Column("export_object_name", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_efth_tenant_employee", "employee_face_training_jobs", ["tenant_id", "employee_id"])
    op.create_index("ix_efth_employee_status", "employee_face_training_jobs", ["employee_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_efth_employee_status", table_name="employee_face_training_jobs")
    op.drop_index("ix_efth_tenant_employee", table_name="employee_face_training_jobs")
    op.drop_table("employee_face_training_jobs")
    op.drop_column("employee_face_templates", "sample_image_object_name")
