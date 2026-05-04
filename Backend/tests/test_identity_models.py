from __future__ import annotations

from pathlib import Path

from airco.models import (
    IdentityCluster,
    IdentityClusterMember,
    IdentityMergeReview,
    SessionPerson,
    SessionPersonTrackBinding,
)


def test_session_person_exposes_merge_support_field():
    assert "merged_into_session_person_id" in SessionPerson.__table__.c
    assert SessionPerson.__table__.c.merged_into_session_person_id.nullable is True


def test_session_person_exposes_identity_cluster_link():
    assert "identity_cluster_id" in SessionPerson.__table__.c
    assert SessionPerson.__table__.c.identity_cluster_id.nullable is True


def test_session_person_track_binding_model_has_canonical_columns():
    table = SessionPersonTrackBinding.__table__

    assert SessionPersonTrackBinding.__tablename__ == "session_person_track_bindings"
    assert {"id", "session_id", "session_person_id", "camera_id", "track_id", "binding_state", "started_at", "last_seen_at", "ended_at", "source", "confidence", "evidence_summary"}.issubset(table.c.keys())


def test_session_person_track_binding_migration_file_exists():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "002_session_person_track_bindings.py"
    )

    assert migration_path.exists()
    migration_text = migration_path.read_text()
    assert 'revision = "002"' in migration_text
    assert "session_person_track_bindings" in migration_text


def test_identity_cluster_model_has_expected_columns():
    table = IdentityCluster.__table__

    assert IdentityCluster.__tablename__ == "identity_clusters"
    assert {
        "id",
        "tenant_id",
        "employee_id",
        "cluster_state",
        "display_label",
        "best_thumbnail_url",
        "face_template",
        "body_template",
        "face_template_updates",
        "body_template_updates",
        "evidence_summary",
        "created_at",
        "updated_at",
        "superseded_by_cluster_id",
    }.issubset(table.c.keys())


def test_identity_cluster_member_model_has_expected_columns():
    table = IdentityClusterMember.__table__

    assert IdentityClusterMember.__tablename__ == "identity_cluster_members"
    assert {
        "id",
        "identity_cluster_id",
        "session_person_id",
        "member_role",
        "joined_at",
        "left_at",
        "active",
    }.issubset(table.c.keys())


def test_identity_merge_review_model_has_expected_columns():
    table = IdentityMergeReview.__table__

    assert IdentityMergeReview.__tablename__ == "identity_merge_reviews"
    assert {
        "id",
        "tenant_id",
        "review_type",
        "decision",
        "source_session_person_id",
        "target_session_person_id",
        "target_employee_id",
        "source_cluster_id",
        "target_cluster_id",
        "reason",
        "evidence_snapshot",
        "created_by",
        "created_at",
        "reverted_by",
        "reverted_at",
        "revert_review_id",
    }.issubset(table.c.keys())


def test_identity_cluster_migration_file_exists():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "004_identity_clusters_and_reviews.py"
    )

    assert migration_path.exists()
    migration_text = migration_path.read_text()
    assert 'revision = "004"' in migration_text
    assert 'down_revision = "003"' in migration_text
    assert "identity_clusters" in migration_text
    assert "identity_cluster_members" in migration_text
    assert "identity_merge_reviews" in migration_text
