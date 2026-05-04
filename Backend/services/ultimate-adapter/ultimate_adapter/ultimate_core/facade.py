from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from .bundle import UltimateCoreBundle, build_ultimate_core_bundle
from .config import UltimateCoreConfig


def _coerce_session_id(value: Any) -> str:
    return str(value)


def _coerce_camera_id(value: Any) -> str:
    return str(value)


def _coerce_topology(topology: Mapping[str, Any] | None) -> dict[str, Any]:
    if not topology:
        return {}
    return dict(topology)


def _coerce_core_config(
    config: Mapping[str, Any] | UltimateCoreConfig | None,
    topology: Mapping[str, Any],
) -> Mapping[str, Any] | UltimateCoreConfig:
    base_config: dict[str, Any]
    if config is None:
        base_config = UltimateCoreConfig().to_dict()
    elif isinstance(config, UltimateCoreConfig):
        base_config = config.to_dict()
    else:
        base_config = dict(config)

    topology_overrides: dict[str, Any] = {}
    for key in (
        "camera_adjacency",
        "camera_transition_sigma",
        "cross_camera_tau_sec",
        "min_travel_time",
        "num_cameras",
        "skip_frames",
    ):
        if key in topology:
            topology_overrides[key] = topology[key]

    nested_overrides = topology.get("core_overrides")
    if isinstance(nested_overrides, Mapping):
        topology_overrides.update(dict(nested_overrides))

    nested_config = topology.get("core_config")
    if isinstance(nested_config, Mapping):
        topology_overrides.update(dict(nested_config))

    base_config.update(topology_overrides)
    return base_config


def _normalize_bbox(x1: Any, y1: Any, x2: Any, y2: Any) -> dict[str, int]:
    return {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}


def _normalize_optional_bbox(bbox: Any) -> list[int] | None:
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    return [int(x1), int(y1), int(x2), int(y2)]


@dataclass(frozen=True)
class IdentitySnapshot:
    global_id: int
    stage: str
    lifecycle: str
    active_track_id: int | None
    birth_camera: str | None = None
    last_camera: str | None = None
    birth_frame: int | None = None
    last_seen_frame: int | None = None
    last_seen_time: float | None = None
    total_detections: int | None = None
    lock_until_frame: int | None = None
    last_match_score: float | None = None
    last_bbox: list[int] | None = None
    last_center: list[float] | None = None
    velocity: list[float] | None = None
    is_occluded: bool | None = None
    occlusion_start_frame: int | None = None
    predicted_bbox: list[int] | None = None
    last_track_key: list[Any] | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrackSnapshot:
    track_id: int | None
    track_key: dict[str, Any]
    bbox: dict[str, int]
    confidence: float
    class_id: int

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UltimateTrackingUpdate:
    session_id: str
    camera_id: str
    frame_index: int
    track: TrackSnapshot
    identity: IdentitySnapshot
    update_type: str = "tracking.identity"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["track"] = self.track.to_dict()
        payload["identity"] = self.identity.to_dict()
        return payload


@dataclass(frozen=True)
class UltimateFrameResult:
    session_id: str
    camera_id: str
    topology: dict[str, Any]
    frame_index: int
    updates: list[UltimateTrackingUpdate]
    births: int
    active_identities: int
    total_identities: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "topology": dict(self.topology),
            "frame_index": self.frame_index,
            "updates": [update.to_dict() for update in self.updates],
            "births": self.births,
            "active_identities": self.active_identities,
            "total_identities": self.total_identities,
        }


@dataclass(frozen=True)
class UltimateCleanupResult:
    session_id: str
    camera_id: str
    frame_index: int
    released_tracks: int
    active_tracks: int
    total_identities: int
    closed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _identity_snapshot(
    identity: Any,
    *,
    global_id: int,
    stage: str,
    track_id: int | None,
) -> IdentitySnapshot:
    if identity is None:
        return IdentitySnapshot(
            global_id=int(global_id),
            stage=str(stage),
            lifecycle="new" if str(stage) == "NEW-BORN" else "tracked",
            active_track_id=track_id,
        )

    return IdentitySnapshot(
        global_id=int(identity.global_id),
        stage=str(stage),
        lifecycle="new" if str(stage) == "NEW-BORN" else "tracked",
        active_track_id=int(identity.active_track_id) if identity.active_track_id is not None else None,
        birth_camera=str(identity.birth_camera),
        last_camera=str(identity.last_camera),
        birth_frame=int(identity.birth_frame),
        last_seen_frame=int(identity.last_seen_frame),
        last_seen_time=float(identity.last_seen_time),
        total_detections=int(identity.total_detections),
        lock_until_frame=int(identity.lock_until_frame),
        last_match_score=float(identity.last_match_score),
        last_bbox=_normalize_optional_bbox(identity.last_bbox),
        last_center=[float(identity.last_center[0]), float(identity.last_center[1])],
        velocity=[float(identity.velocity[0]), float(identity.velocity[1])],
        is_occluded=bool(identity.is_occluded),
        occlusion_start_frame=int(identity.occlusion_start_frame),
        predicted_bbox=_normalize_optional_bbox(identity.predicted_bbox),
        last_track_key=list(identity.last_track_key) if identity.last_track_key is not None else None,
    )


class UltimateAdapterFacade:
    def __init__(
        self,
        *,
        session_id: Any,
        camera_id: Any,
        topology: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | UltimateCoreConfig | None = None,
        detector=None,
        tracker_backend=None,
        feature_extractor=None,
        registry=None,
    ):
        self.session_id = _coerce_session_id(session_id)
        self.camera_id = _coerce_camera_id(camera_id)
        self.topology = _coerce_topology(topology)
        core_config = _coerce_core_config(config, self.topology)
        self.bundle: UltimateCoreBundle = build_ultimate_core_bundle(
            core_config,
            camera_id=self.camera_id,
            detector=detector,
            tracker_backend=tracker_backend,
            feature_extractor=feature_extractor,
            registry=registry,
        )
        self._closed = False
        self._last_frame_index = 0
        self._last_cleanup: UltimateCleanupResult | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()
        return False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ultimate adapter facade has been closed")

    def _normalize_update(self, raw_update: Sequence[Any], frame_index: int) -> UltimateTrackingUpdate:
        x1, y1, x2, y2, global_id, confidence, class_id, stage = raw_update
        identity = self.bundle.registry.get_identity(int(global_id)) if self.bundle.registry is not None else None
        track_id = None
        if identity is not None and identity.active_track_id is not None:
            track_id = int(identity.active_track_id)
        return UltimateTrackingUpdate(
            session_id=self.session_id,
            camera_id=self.camera_id,
            frame_index=int(frame_index),
            track=TrackSnapshot(
                track_id=track_id,
                track_key={
                    "session_id": self.session_id,
                    "camera_id": self.camera_id,
                    "track_id": track_id,
                },
                bbox=_normalize_bbox(x1, y1, x2, y2),
                confidence=float(confidence),
                class_id=int(class_id),
            ),
            identity=_identity_snapshot(identity, global_id=int(global_id), stage=str(stage), track_id=track_id),
        )

    def update(self, frame: np.ndarray, detections: Optional[np.ndarray] = None) -> UltimateFrameResult:
        self._ensure_open()
        if detections is None:
            raw_updates = self.bundle.tracker.track_frame(frame)
        else:
            detections_array = np.asarray(detections, dtype=np.float32)
            if detections_array.ndim == 1:
                if detections_array.size == 0:
                    detections_array = np.empty((0, 6), dtype=np.float32)
                else:
                    detections_array = detections_array.reshape(1, -1)
            raw_updates = self.bundle.tracker.process_detections(frame, detections_array)

        frame_index = int(self.bundle.tracker.frame_idx)
        self._last_frame_index = frame_index
        updates = [self._normalize_update(raw_update, frame_index) for raw_update in raw_updates]
        registry = self.bundle.registry
        active_identities = registry.get_active_count() if registry is not None else 0
        total_identities = registry.get_total_count() if registry is not None else 0
        births = sum(1 for update in updates if update.identity.lifecycle == "new")
        return UltimateFrameResult(
            session_id=self.session_id,
            camera_id=self.camera_id,
            topology=dict(self.topology),
            frame_index=frame_index,
            updates=updates,
            births=births,
            active_identities=active_identities,
            total_identities=total_identities,
        )

    def process_frame(self, frame: np.ndarray, detections: Optional[np.ndarray] = None) -> UltimateFrameResult:
        return self.update(frame, detections=detections)

    def update_frame(self, frame: np.ndarray, detections: Optional[np.ndarray] = None) -> UltimateFrameResult:
        return self.update(frame, detections=detections)

    def _cleanup(self) -> UltimateCleanupResult:
        registry = self.bundle.registry
        before_active = registry.get_active_count() if registry is not None else 0
        if registry is not None:
            if hasattr(registry, "release_camera_tracks"):
                registry.release_camera_tracks(self.camera_id, self._last_frame_index)
            if hasattr(registry.gallery, "flush"):
                registry.gallery.flush()
        self.bundle.tracker.birth_system.cleanup(set(), self._last_frame_index)
        self.bundle.tracker.feature_cache.clear()
        self.bundle.tracker._last_results = []
        self.bundle.tracker.trails.clear()
        active_identities = registry.get_active_count() if registry is not None else 0
        total_identities = registry.get_total_count() if registry is not None else 0
        return UltimateCleanupResult(
            session_id=self.session_id,
            camera_id=self.camera_id,
            frame_index=self._last_frame_index,
            released_tracks=max(0, before_active - active_identities),
            active_tracks=active_identities,
            total_identities=total_identities,
        )

    def close(self) -> UltimateCleanupResult:
        if self._closed:
            if self._last_cleanup is not None:
                return self._last_cleanup
            return UltimateCleanupResult(
                session_id=self.session_id,
                camera_id=self.camera_id,
                frame_index=self._last_frame_index,
                released_tracks=0,
                active_tracks=0,
                total_identities=0,
                closed=True,
            )

        cleanup_result = self._cleanup()
        self.bundle.close()
        self._closed = True
        self._last_cleanup = UltimateCleanupResult(
            session_id=cleanup_result.session_id,
            camera_id=cleanup_result.camera_id,
            frame_index=cleanup_result.frame_index,
            released_tracks=cleanup_result.released_tracks,
            active_tracks=cleanup_result.active_tracks,
            total_identities=cleanup_result.total_identities,
            closed=True,
        )
        return self._last_cleanup

    def shutdown(self) -> UltimateCleanupResult:
        return self.close()

    def cleanup(self) -> UltimateCleanupResult:
        return self.close()


UltimateAdapterCoreFacade = UltimateAdapterFacade
