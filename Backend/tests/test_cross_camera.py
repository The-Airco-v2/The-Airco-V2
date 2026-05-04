"""Test cross-camera identity merge scoring.

From 2.0-project.md: merge score must include body-ReID similarity,
face evidence, elapsed time, camera-pair prior, impossible-travel penalty,
and overlap/simultaneous-view handling.
"""

import pytest
from identity_consumer.cross_camera import CrossCameraMerger, CameraTopology


@pytest.fixture
def merger():
    topology = CameraTopology()
    topology.add_pair("cam_1", "cam_2", overlap_type="adjacent", transition_min=5, transition_max=60)
    topology.add_pair("cam_1", "cam_3", overlap_type="distant", transition_min=30, transition_max=300)
    topology.add_pair("cam_2", "cam_3", overlap_type="overlapping", transition_min=0, transition_max=0, same_space=True)
    return CrossCameraMerger(topology)


def test_adjacent_camera_reasonable_time(merger):
    score = merger.compute_merge_score(
        body_similarity=0.75,
        face_similarity=0.80,
        elapsed_seconds=15,
        camera_a="cam_1",
        camera_b="cam_2",
    )
    assert score > 0.5, "Adjacent cameras with reasonable transition should score high"


def test_distant_camera_impossible_travel(merger):
    score = merger.compute_merge_score(
        body_similarity=0.75,
        face_similarity=0.80,
        elapsed_seconds=2,  # impossibly fast for distant cameras
        camera_a="cam_1",
        camera_b="cam_3",
    )
    assert score < 0.3, "Impossible travel should heavily penalize merge score"


def test_overlapping_cameras_simultaneous_view(merger):
    score = merger.compute_merge_score(
        body_similarity=0.70,
        face_similarity=0.0,  # no face, just body
        elapsed_seconds=0,  # simultaneous
        camera_a="cam_2",
        camera_b="cam_3",
    )
    assert score > 0.4, "Overlapping cameras allow simultaneous presence"


def test_strong_body_similarity_without_face_can_still_reassociate(merger):
    score = merger.compute_merge_score(
        body_similarity=0.96,
        face_similarity=0.0,
        elapsed_seconds=12,
        camera_a="cam_1",
        camera_b="cam_2",
    )
    assert score >= merger.REASSOCIATION_MIN_SCORE, (
        "Strong body ReID evidence on adjacent cameras should be enough even without face evidence"
    )


def test_no_body_similarity_low_score(merger):
    score = merger.compute_merge_score(
        body_similarity=0.2,
        face_similarity=0.0,
        elapsed_seconds=15,
        camera_a="cam_1",
        camera_b="cam_2",
    )
    assert score < 0.3, "Low body similarity should produce low merge score"


from identity_consumer.cross_camera import ZoneInfo

def test_zone_gating_penalizes_incompatible_cameras():
    topology = CameraTopology()
    topology.add_pair("cam_1", "cam_2", overlap_type="adjacent", transition_min=5, transition_max=60)
    zones = [
        ZoneInfo(camera_id="cam_1", zone_type="exit", connects_to_camera_id="cam_3"),
        ZoneInfo(camera_id="cam_2", zone_type="entry", connects_to_camera_id="cam_3"),
    ]
    merger = CrossCameraMerger(topology, zones=zones)
    score = merger.compute_merge_score(body_similarity=0.85, face_similarity=0.80, elapsed_seconds=15, camera_a="cam_1", camera_b="cam_2")
    assert score < 0.2

def test_zone_gating_allows_compatible_cameras():
    topology = CameraTopology()
    topology.add_pair("cam_1", "cam_2", overlap_type="adjacent", transition_min=5, transition_max=60)
    zones = [
        ZoneInfo(camera_id="cam_1", zone_type="exit", connects_to_camera_id="cam_2"),
        ZoneInfo(camera_id="cam_2", zone_type="entry", connects_to_camera_id="cam_1"),
    ]
    merger = CrossCameraMerger(topology, zones=zones)
    score = merger.compute_merge_score(body_similarity=0.85, face_similarity=0.80, elapsed_seconds=15, camera_a="cam_1", camera_b="cam_2")
    assert score > 0.5

def test_zone_gating_graceful_when_no_zones():
    topology = CameraTopology()
    topology.add_pair("cam_1", "cam_2", overlap_type="adjacent", transition_min=5, transition_max=60)
    merger = CrossCameraMerger(topology, zones=None)
    score = merger.compute_merge_score(body_similarity=0.85, face_similarity=0.80, elapsed_seconds=15, camera_a="cam_1", camera_b="cam_2")
    assert score > 0.5
