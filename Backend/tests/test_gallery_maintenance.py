"""Tests for gallery maintenance."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
import numpy as np
import pytest
from identity_consumer.gallery_maintenance import GalleryMaintenance

def _make_person(*, person_id, session_id, state="unknown", last_seen_at, is_active=True):
    return SimpleNamespace(id=person_id, session_id=session_id, recognition_state=state,
        last_seen_at=last_seen_at, is_active=is_active, merged_into_session_person_id=None)

def test_expire_stale_unknowns():
    now = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    stale = _make_person(person_id=uuid.uuid4(), session_id=uuid.uuid4(), last_seen_at=now - timedelta(days=10))
    fresh = _make_person(person_id=uuid.uuid4(), session_id=uuid.uuid4(), last_seen_at=now - timedelta(hours=2))
    m = GalleryMaintenance(ttl_days=7)
    assert m.should_expire(stale, now) is True
    assert m.should_expire(fresh, now) is False

def test_identified_persons_never_expire():
    now = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    identified = _make_person(person_id=uuid.uuid4(), session_id=uuid.uuid4(), state="identified", last_seen_at=now - timedelta(days=30))
    m = GalleryMaintenance(ttl_days=7)
    assert m.should_expire(identified, now) is False

def test_propose_merges_finds_similar_unknowns():
    m = GalleryMaintenance()
    e1 = np.zeros(512, dtype=np.float32); e1[0] = 1.0
    e2 = np.zeros(512, dtype=np.float32); e2[0] = 0.99; e2[1] = 0.14
    e3 = np.zeros(512, dtype=np.float32); e3[100] = 1.0
    p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    gallery = {p1: e1, p2: e2, p3: e3}
    proposals = m.find_merge_candidates(gallery, threshold=0.85)
    assert len(proposals) == 1
    assert {proposals[0][0], proposals[0][1]} == {p1, p2}
