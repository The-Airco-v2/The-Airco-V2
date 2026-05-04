"""Tests for three-band threshold FaceMatcher."""

from __future__ import annotations

import math

import numpy as np
import pytest

from identity_consumer.face_matcher import FaceMatcher, MatchBand

DIM = 512


def _make_template(score: float, dim: int = DIM) -> np.ndarray:
    """Build a unit vector whose cosine similarity with [1,0,...,0] equals `score`."""
    vec = np.zeros(dim, dtype=np.float32)
    vec[0] = score
    vec[1] = math.sqrt(1.0 - score * score)
    return vec


QUERY = np.zeros(DIM, dtype=np.float32)
QUERY[0] = 1.0  # unit vector along first axis


class TestThreeBandThresholds:
    def test_accept_band_above_threshold(self):
        templates = {"emp-1": [_make_template(0.72)]}
        matcher = FaceMatcher()
        results = matcher.find_top_matches(QUERY, templates)
        assert len(results) == 1
        assert results[0]["band"] == MatchBand.ACCEPT
        assert results[0]["score"] == pytest.approx(0.72, abs=0.01)

    def test_uncertain_band_between_thresholds(self):
        templates = {"emp-1": [_make_template(0.58)]}
        matcher = FaceMatcher()
        results = matcher.find_top_matches(QUERY, templates)
        assert len(results) == 1
        assert results[0]["band"] == MatchBand.UNCERTAIN
        assert results[0]["score"] == pytest.approx(0.58, abs=0.01)

    def test_reject_band_below_threshold(self):
        templates = {"emp-1": [_make_template(0.40)]}
        matcher = FaceMatcher()
        results = matcher.find_top_matches(QUERY, templates)
        assert len(results) == 0

    def test_margin_test_forces_uncertain_when_top_two_close(self):
        """Scores 0.70 and 0.67 differ by 0.03 < margin 0.08 => best demoted to UNCERTAIN."""
        templates = {
            "emp-1": [_make_template(0.70)],
            "emp-2": [_make_template(0.67)],
        }
        matcher = FaceMatcher()
        results = matcher.find_top_matches(QUERY, templates)
        assert len(results) == 2
        # Best result should be demoted to UNCERTAIN
        assert results[0]["band"] == MatchBand.UNCERTAIN
        assert results[0]["score"] == pytest.approx(0.70, abs=0.01)

    def test_margin_test_passes_when_clear_winner(self):
        """Scores 0.75 and 0.55 differ by 0.20 > margin 0.08 => best stays ACCEPT."""
        templates = {
            "emp-1": [_make_template(0.75)],
            "emp-2": [_make_template(0.55)],
        }
        matcher = FaceMatcher()
        results = matcher.find_top_matches(QUERY, templates)
        assert len(results) == 2
        assert results[0]["band"] == MatchBand.ACCEPT
        assert results[0]["score"] == pytest.approx(0.75, abs=0.01)
        # Second result is below accept threshold
        assert results[1]["band"] == MatchBand.UNCERTAIN

    def test_custom_thresholds_override_defaults(self):
        """Per-call overrides: accept=0.55, uncertain=0.40."""
        templates = {"emp-1": [_make_template(0.58)]}
        matcher = FaceMatcher()
        results = matcher.find_top_matches(
            QUERY, templates, accept_threshold=0.55, uncertain_threshold=0.40
        )
        assert len(results) == 1
        assert results[0]["band"] == MatchBand.ACCEPT

    def test_backward_compat_similarity_threshold(self):
        """Old code passing similarity_threshold maps to uncertain_threshold."""
        matcher = FaceMatcher(similarity_threshold=0.55)
        assert matcher.threshold == 0.55
        assert matcher.uncertain_threshold == 0.55
        assert matcher.accept_threshold == 0.65

    def test_match_band_enum_values(self):
        assert MatchBand.ACCEPT == "accept"
        assert MatchBand.UNCERTAIN == "uncertain"


# --- State machine three-band integration tests ---

import uuid
from identity_consumer.state_machine import IdentityStateMachine


def test_uncertain_evidence_does_not_promote_to_candidate():
    sm = IdentityStateMachine()
    emp_id = uuid.uuid4()
    transition = sm.process_face_evidence(employee_id=emp_id, similarity=0.58, observation_count=1, band="uncertain")
    assert transition.new_state == "unknown"


def test_accept_evidence_promotes_to_candidate():
    sm = IdentityStateMachine()
    emp_id = uuid.uuid4()
    transition = sm.process_face_evidence(employee_id=emp_id, similarity=0.68, observation_count=1, band="accept")
    assert transition.new_state == "candidate"


def test_uncertain_evidence_still_recorded_for_promotion():
    sm = IdentityStateMachine()
    emp_id = uuid.uuid4()
    sm.process_face_evidence(employee_id=emp_id, similarity=0.70, observation_count=1, band="accept")
    sm.process_face_evidence(employee_id=emp_id, similarity=0.60, observation_count=2, band="uncertain")
    sm.process_face_evidence(employee_id=emp_id, similarity=0.62, observation_count=3, band="uncertain")
    transition = sm.process_face_evidence(employee_id=emp_id, similarity=0.70, observation_count=4, band="accept")
    assert transition.new_state == "candidate"  # Only 2 accept observations
    transition = sm.process_face_evidence(employee_id=emp_id, similarity=0.72, observation_count=5, band="accept")
    assert transition.new_state == "identified"  # Now 3 accept observations
