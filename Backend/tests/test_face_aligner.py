"""Tests for 5-point face alignment."""
from __future__ import annotations
import sys
from pathlib import Path
# Add savant-pipeline to path so we can import face_aligner
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "savant-pipeline"))
import numpy as np
import pytest

def test_align_face_produces_112x112_output():
    from face_aligner import align_face
    img = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    keypoints = np.array([[60, 70], [140, 70], [100, 110], [70, 150], [130, 150]], dtype=np.float32)
    result = align_face(img, keypoints)
    assert result is not None
    assert result.shape == (112, 112, 3)
    assert result.dtype == np.uint8

def test_align_face_returns_none_for_degenerate_keypoints():
    from face_aligner import align_face
    img = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    keypoints = np.array([[100, 100]] * 5, dtype=np.float32)
    result = align_face(img, keypoints)
    assert result is None

def test_align_face_rejects_wrong_keypoint_count():
    from face_aligner import align_face
    img = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    keypoints = np.array([[60, 70], [140, 70], [100, 110]], dtype=np.float32)
    result = align_face(img, keypoints)
    assert result is None
