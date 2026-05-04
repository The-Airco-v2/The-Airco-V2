"""Identity clusters and operator review audit.

Revision ID: 004
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_clusters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column(
            "cluster_state",
            sa.Enum("anonymous", "employee_linked", "superseded", name="identity_cluster_state_enum"),
            nullable=False,
            server_default=sa.text("'anonymous'"),
        ),
        sa.Column("display_label", sa.String(length=255), nullable=False, server_default=sa.text("'Unknown Identity Cluster'")),
        sa.Column("best_thumbnail_url", sa.Text(), nullable=True),
        sa.Column("face_template", Vector(512), nullable=True),
        sa.Column("body_template", Vector(512), nullable=True),
        sa.Column("face_template_updates", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("body_template_updates", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence_summary", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_by_cluster_id", UUID(as_uuid=True), sa.ForeignKey("identity_clusters.id"), nullable=True),
    )
    op.create_index("ix_ic_tenant_state", "identity_clusters", ["tenant_id", "cluster_state"])
    op.create_index("ix_ic_employee", "identity_clusters", ["employee_id"])

    op.add_column(
        "session_persons",
        sa.Column("identity_cluster_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_session_persons_identity_cluster_id",
        "session_persons",
        "identity_clusters",
        ["identity_cluster_id"],
        ["id"],
    )
    op.create_index("ix_sp_identity_cluster", "session_persons", ["identity_cluster_id"])

    op.create_table(
        "identity_cluster_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("identity_cluster_id", UUID(as_uuid=True), sa.ForeignKey("identity_clusters.id"), nullable=False),
        sa.Column("session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=False),
        sa.Column(
            "member_role",
            sa.Enum("seed", "merged", "employee_assignment", name="identity_cluster_member_role_enum"),
            nullable=False,
            server_default=sa.text("'seed'"),
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_icm_cluster_active", "identity_cluster_members", ["identity_cluster_id", "active"])
    op.create_index("ix_icm_session_person_active", "identity_cluster_members", ["session_person_id", "active"])

    op.create_table(
        "identity_merge_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "review_type",
            sa.Enum("unknown_merge", "assign_employee", "split_member", "undo_review", name="identity_review_type_enum"),
            nullable=False,
            server_default=sa.text("'unknown_merge'"),
        ),
        sa.Column(
            "decision",
            sa.Enum("confirmed", "reverted", name="identity_review_decision_enum"),
            nullable=False,
            server_default=sa.text("'confirmed'"),
        ),
        sa.Column("source_session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=False),
        sa.Column("target_session_person_id", UUID(as_uuid=True), sa.ForeignKey("session_persons.id"), nullable=True),
        sa.Column("target_employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("source_cluster_id", UUID(as_uuid=True), sa.ForeignKey("identity_clusters.id"), nullable=True),
        sa.Column("target_cluster_id", UUID(as_uuid=True), sa.ForeignKey("identity_clusters.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_snapshot", JSONB, nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reverted_by", sa.String(length=255), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revert_review_id", UUID(as_uuid=True), sa.ForeignKey("identity_merge_reviews.id"), nullable=True),
    )
    op.create_index("ix_imr_tenant_created", "identity_merge_reviews", ["tenant_id", "created_at"])
    op.create_index("ix_imr_source_person", "identity_merge_reviews", ["source_session_person_id"])


def downgrade() -> None:
    op.drop_index("ix_imr_source_person", table_name="identity_merge_reviews")
    op.drop_index("ix_imr_tenant_created", table_name="identity_merge_reviews")
    op.drop_table("identity_merge_reviews")

    op.drop_index("ix_icm_session_person_active", table_name="identity_cluster_members")
    op.drop_index("ix_icm_cluster_active", table_name="identity_cluster_members")
    op.drop_table("identity_cluster_members")

    op.drop_index("ix_sp_identity_cluster", table_name="session_persons")
    op.drop_constraint("fk_session_persons_identity_cluster_id", "session_persons", type_="foreignkey")
    op.drop_column("session_persons", "identity_cluster_id")

    op.drop_index("ix_ic_employee", table_name="identity_clusters")
    op.drop_index("ix_ic_tenant_state", table_name="identity_clusters")
    op.drop_table("identity_clusters")

    op.execute("DROP TYPE IF EXISTS identity_review_decision_enum")
    op.execute("DROP TYPE IF EXISTS identity_review_type_enum")
    op.execute("DROP TYPE IF EXISTS identity_cluster_member_role_enum")
    op.execute("DROP TYPE IF EXISTS identity_cluster_state_enum")
