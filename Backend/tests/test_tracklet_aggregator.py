"""Tests for EMA tracklet embedding aggregation."""
from __future__ import annotations

import uuid

import numpy as np
import pytest

from identity_consumer.tracklet_aggregator import TrackletAggregator, TrackletTemplate


def _unit_vec(dim: int = 512, idx: int = 0) -> np.ndarray:
    """Create a zero vector with 1.0 at given index."""
    v = np.zeros(dim, dtype=np.float32)
    v[idx] = 1.0
    return v


class TestTrackletAggregator:
    def test_first_update_sets_template_directly(self):
        agg = TrackletAggregator()
        pid = uuid.uuid4()
        emb = _unit_vec(idx=0)
        tpl = agg.update(pid, "face", emb, quality=0.8)

        assert tpl.update_count == 1
        np.testing.assert_array_almost_equal(tpl.embedding, emb)
        assert tpl.best_quality == 0.8
        np.testing.assert_array_almost_equal(tpl.best_embedding, emb)

    def test_ema_blends_toward_new_embedding(self):
        agg = TrackletAggregator()
        pid = uuid.uuid4()
        e1 = _unit_vec(idx=0)
        e2 = _unit_vec(idx=1)

        agg.update(pid, "face", e1, quality=1.0)
        tpl = agg.update(pid, "face", e2, quality=1.0)

        assert tpl.update_count == 2
        # Template should have components from both vectors
        assert tpl.embedding[0] > 0.0
        assert tpl.embedding[1] > 0.0
        # Should be L2-normalized
        norm = np.linalg.norm(tpl.embedding)
        assert abs(norm - 1.0) < 1e-6

    def test_low_quality_has_less_influence(self):
        agg_high = TrackletAggregator()
        agg_low = TrackletAggregator()
        pid = uuid.uuid4()
        e1 = _unit_vec(idx=0)
        e2 = _unit_vec(idx=1)

        # High quality second observation
        agg_high.update(pid, "face", e1, quality=1.0)
        tpl_high = agg_high.update(pid, "face", e2, quality=1.0)

        # Low quality second observation
        agg_low.update(pid, "face", e1, quality=1.0)
        tpl_low = agg_low.update(pid, "face", e2, quality=0.1)

        # Low quality should have less influence: component at idx=1 should be smaller
        assert tpl_low.embedding[1] < tpl_high.embedding[1]
        # And more of the original at idx=0
        assert tpl_low.embedding[0] > tpl_high.embedding[0]

    def test_best_quality_tracking(self):
        agg = TrackletAggregator()
        pid = uuid.uuid4()
        e1 = _unit_vec(idx=0)
        e2 = _unit_vec(idx=1)
        e3 = _unit_vec(idx=2)

        agg.update(pid, "face", e1, quality=0.5)
        agg.update(pid, "face", e2, quality=0.9)
        tpl = agg.update(pid, "face", e3, quality=0.3)

        assert tpl.best_quality == 0.9
        np.testing.assert_array_almost_equal(tpl.best_embedding, e2)

    def test_separate_modalities(self):
        agg = TrackletAggregator()
        pid = uuid.uuid4()
        e_face = _unit_vec(idx=0)
        e_body = _unit_vec(idx=1)

        agg.update(pid, "face", e_face, quality=0.8)
        agg.update(pid, "body", e_body, quality=0.7)

        tpl_face = agg.get_template(pid, "face")
        tpl_body = agg.get_template(pid, "body")

        assert tpl_face is not None
        assert tpl_body is not None
        np.testing.assert_array_almost_equal(tpl_face.embedding, e_face)
        np.testing.assert_array_almost_equal(tpl_body.embedding, e_body)

    def test_get_template_returns_none_for_unknown(self):
        agg = TrackletAggregator()
        assert agg.get_template(uuid.uuid4(), "face") is None
