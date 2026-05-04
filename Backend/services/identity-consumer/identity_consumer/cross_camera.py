"""Cross-camera identity merge scoring.

Uses camera topology metadata + body/face similarity + timing
to produce a merge confidence score [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CameraPairInfo:
    overlap_type: str = "distant"  # overlapping, adjacent, distant
    transition_min: float = 0.0
    transition_max: float = 300.0
    same_space: bool = False


@dataclass
class ZoneInfo:
    """Exit/entry zone for a camera."""
    camera_id: str
    zone_type: str  # entry, exit, door
    connects_to_camera_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class CameraTopology:
    """Camera pair metadata for merge scoring."""

    def __init__(self):
        self._pairs: dict[tuple[str, str], CameraPairInfo] = {}

    def add_pair(self, cam_a: str, cam_b: str, overlap_type: str = "distant",
                 transition_min: float = 0.0, transition_max: float = 300.0,
                 same_space: bool = False, overwrite: bool = True):
        key = tuple(sorted([cam_a, cam_b]))
        if key in self._pairs and not overwrite:
            return
        self._pairs[key] = CameraPairInfo(
            overlap_type=overlap_type,
            transition_min=transition_min,
            transition_max=transition_max,
            same_space=same_space,
        )

    def get_pair(self, cam_a: str, cam_b: str) -> CameraPairInfo:
        key = tuple(sorted([cam_a, cam_b]))
        return self._pairs.get(key, CameraPairInfo())


class CrossCameraMerger:
    """Compute merge scores for cross-camera identity candidates."""

    # Weight configuration for evidence components
    W_BODY = 0.55
    W_FACE = 0.45
    REASSOCIATION_MIN_SCORE = 0.80
    REASSOCIATION_MARGIN = 0.10

    def __init__(self, topology: CameraTopology, zones: list[ZoneInfo] | None = None):
        self.topology = topology
        self._zones_by_camera: dict[str, list[ZoneInfo]] = {}
        if zones:
            for z in zones:
                self._zones_by_camera.setdefault(z.camera_id, []).append(z)

    def compute_merge_score(
        self,
        body_similarity: float,
        face_similarity: float,
        elapsed_seconds: float,
        camera_a: str,
        camera_b: str,
    ) -> float:
        """Compute weighted merge score [0, 1]."""
        pair = self.topology.get_pair(camera_a, camera_b)

        # Evidence score: reweight to the modalities that are actually present.
        body_score = max(0.0, body_similarity)
        face_score = max(0.0, face_similarity)
        evidence = self._appearance_evidence(body_score=body_score, face_score=face_score)

        # Timing acts as a multiplicative modifier on evidence
        timing_score = self._timing_score(elapsed_seconds, pair)

        # Topology provides a small additive bonus
        topo_bonus = self._topology_bonus(pair)

        raw = evidence * timing_score + topo_bonus

        # Impossible travel is a hard penalty
        if self._is_impossible_travel(elapsed_seconds, pair):
            raw *= 0.2

        if not self._zone_compatible(camera_a, camera_b):
            raw *= 0.1

        return min(1.0, max(0.0, raw))

    def _appearance_evidence(self, *, body_score: float, face_score: float) -> float:
        if body_score > 0.0 and face_score > 0.0:
            total_weight = self.W_BODY + self.W_FACE
            return ((self.W_BODY * body_score) + (self.W_FACE * face_score)) / total_weight
        if body_score > 0.0:
            return body_score
        if face_score > 0.0:
            return face_score
        return 0.0

    def is_confident_reassociation(
        self,
        score: float,
        *,
        runner_up_score: float | None = None,
    ) -> bool:
        if score < self.REASSOCIATION_MIN_SCORE:
            return False
        if runner_up_score is not None and (score - runner_up_score) < self.REASSOCIATION_MARGIN:
            return False
        return True

    def _timing_score(self, elapsed: float, pair: CameraPairInfo) -> float:
        if pair.same_space or pair.overlap_type == "overlapping":
            return 1.0  # simultaneous presence is expected

        if elapsed < pair.transition_min:
            return 0.1  # too fast
        if elapsed > pair.transition_max:
            return 0.3  # too slow but possible

        # Within expected range
        return 1.0

    def _topology_bonus(self, pair: CameraPairInfo) -> float:
        if pair.overlap_type == "overlapping" or pair.same_space:
            return 0.10
        if pair.overlap_type == "adjacent":
            return 0.05
        return 0.0  # distant — no bonus

    def _zone_compatible(self, camera_a: str, camera_b: str) -> bool:
        """Check if cameras have compatible exit/entry zones.
        Returns True if no zones configured (graceful fallback),
        or if camera_a has exit connecting to camera_b,
        or if camera_b has entry connecting to camera_a.
        """
        if not self._zones_by_camera:
            return True
        a_zones = self._zones_by_camera.get(camera_a, [])
        b_zones = self._zones_by_camera.get(camera_b, [])
        if not a_zones and not b_zones:
            return True
        for z in a_zones:
            if z.zone_type in ("exit", "door") and z.connects_to_camera_id == camera_b:
                return True
        for z in b_zones:
            if z.zone_type in ("entry", "door") and z.connects_to_camera_id == camera_a:
                return True
        return False

    def _is_impossible_travel(self, elapsed: float, pair: CameraPairInfo) -> bool:
        if pair.same_space or pair.overlap_type == "overlapping":
            return False
        return elapsed < pair.transition_min * 0.5  # 50% safety margin
