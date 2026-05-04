"""Test face embedding comparison using cosine similarity."""

import numpy as np
import pytest
from identity_consumer.face_matcher import FaceMatcher


@pytest.fixture
def matcher():
    return FaceMatcher(similarity_threshold=0.55)


def test_identical_embeddings_match(matcher):
    emb = np.random.randn(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    score = matcher.cosine_similarity(emb, emb)
    assert score > 0.99


def test_orthogonal_embeddings_dont_match(matcher):
    emb_a = np.zeros(512, dtype=np.float32)
    emb_a[0] = 1.0
    emb_b = np.zeros(512, dtype=np.float32)
    emb_b[1] = 1.0
    score = matcher.cosine_similarity(emb_a, emb_b)
    assert score < 0.01


def test_top_k_matches_returns_sorted(matcher):
    query = np.random.randn(512).astype(np.float32)
    templates = {
        "emp_1": [query + np.random.randn(512) * 0.1],  # very similar
        "emp_2": [np.random.randn(512)],  # random
        "emp_3": [query + np.random.randn(512) * 0.05],  # even more similar
    }
    results = matcher.find_top_matches(query, templates, top_k=3)
    # emp_3 should be first (closest), emp_1 second
    assert len(results) <= 3
    assert results[0]["score"] >= results[1]["score"]
