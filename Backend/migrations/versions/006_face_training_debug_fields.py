"""Face training debug fields.

Revision ID: 006
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employee_face_training_jobs", sa.Column("debug_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("employee_face_training_jobs", sa.Column("detector_face_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("employee_face_training_jobs", sa.Column("detector_confidence", sa.Float(), nullable=True))
    op.add_column("employee_face_training_jobs", sa.Column("detector_bbox", JSONB, nullable=True))
    op.add_column("employee_face_training_jobs", sa.Column("rejection_reason", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("employee_face_training_jobs", "rejection_reason")
    op.drop_column("employee_face_training_jobs", "detector_bbox")
    op.drop_column("employee_face_training_jobs", "detector_confidence")
    op.drop_column("employee_face_training_jobs", "detector_face_count")
    op.drop_column("employee_face_training_jobs", "debug_mode")
