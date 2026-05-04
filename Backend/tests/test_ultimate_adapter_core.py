from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
import pytest

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from ultimate_adapter.ultimate_core import (  # noqa: E402
    BirthCertificateSystem,
    PersonEmbeddingCodec,
    PersistentEmbeddingGallery,
    UltimateCoreConfig,
    UltimateAdapterCoreFacade,
)


def _make_embedding(seed: float) -> np.ndarray:
    return np.asarray([seed, seed + 1.0, seed + 2.0], dtype=np.float32)


def test_core_config_wrapper_applies_overrides_and_preserves_defaults():
    cfg = UltimateCoreConfig(overrides={"device": "cuda:1", "use_multiscale_reid": True})
    mapping = cfg.to_dict()

    assert mapping["device"] == "cuda:1"
    assert mapping["use_multiscale_reid"] is True
    assert mapping["bank_max_embeds"] == 25
    assert mapping["birth_min_frames"] == 2


def test_birth_certificate_system_validates_and_expires_candidates():
    cfg = {
        "birth_min_frames": 2,
        "birth_min_conf": 0.4,
        "birth_max_frames": 5,
    }
    system = BirthCertificateSystem(cfg)
    track_key = ("cam-1", 11)

    first = system.add_observation(track_key, _make_embedding(1.0), _make_embedding(10.0), (1, 2, 3, 4), 0.5, 1)
    second = system.add_observation(track_key, _make_embedding(2.0), _make_embedding(20.0), (1, 2, 3, 4), 0.6, 2)

    assert first is None
    assert second is not None
    assert second.is_valid(cfg, 2)

    system.cleanup(active_tracks=set(), frame_idx=20)
    assert track_key not in system.candidates


def test_person_embedding_codec_round_trips_identity_fields():
    identity = type(
        "Identity",
        (),
        {
            "global_id": 3,
            "birth_camera": "1",
            "last_camera": "4",
            "last_seen_time": 50.25,
            "total_detections": 2,
            "seed_embedding": _make_embedding(1.0),
            "ema_embedding": _make_embedding(2.0),
            "viewpoint_bank": deque([_make_embedding(3.0)], maxlen=25),
            "viewpoint_cameras": deque(["1"], maxlen=25),
            "color_signature": _make_embedding(4.0),
            "birth_frame": 5,
            "last_seen_frame": 6,
            "lock_until_frame": 9,
            "last_match_score": 0.83,
            "last_bbox": (1.0, 2.0, 3.0, 4.0),
            "last_center": (5.0, 6.0),
            "velocity": (0.5, 0.25),
        },
    )()

    payload = PersonEmbeddingCodec.encode_identity(identity)
    decoded = PersonEmbeddingCodec.decode_identity(payload)

    assert decoded["global_id"] == 3
    assert decoded["last_camera"] == "4"
    assert decoded["last_bbox"] == (1.0, 2.0, 3.0, 4.0)
    assert decoded["viewpoint_bank"]


def test_persistent_embedding_gallery_round_trips_identity(tmp_path):
    gallery = PersistentEmbeddingGallery(str(tmp_path))
    try:
        identity = type(
            "Identity",
            (),
            {
                "global_id": 7,
                "birth_camera": "1",
                "last_camera": "2",
                "last_seen_time": 123.5,
                "total_detections": 4,
                "seed_embedding": _make_embedding(1.0),
                "ema_embedding": _make_embedding(2.0),
                "viewpoint_bank": deque([_make_embedding(3.0)], maxlen=25),
                "viewpoint_cameras": deque(["1"], maxlen=25),
                "color_signature": _make_embedding(4.0),
                "birth_frame": 8,
                "last_seen_frame": 12,
                "lock_until_frame": 18,
                "last_match_score": 0.91,
                "last_bbox": (1.0, 2.0, 3.0, 4.0),
                "last_center": (5.0, 6.0),
                "velocity": (0.5, 0.25),
            },
        )()

        gallery.save_identity(identity)
        gallery.flush()

        loaded = gallery.load_identity(7)
        assert loaded is not None
        assert loaded["global_id"] == 7
        assert loaded["birth_camera"] == "1"
        assert loaded["last_camera"] == "2"
        assert loaded["last_bbox"] == (1.0, 2.0, 3.0, 4.0)
        assert loaded["viewpoint_bank"]
    finally:
        gallery.stop()


def test_facade_emits_structured_updates_and_supports_cleanup(tmp_path):
    cfg = UltimateCoreConfig(
        overrides={
            "device": "cpu",
            "embedding_storage_dir": str(tmp_path),
            "birth_min_frames": 1,
            "birth_min_conf": 0.0,
            "use_ukf": False,
            "use_transformer_motion": False,
            "use_temporal_consistency": False,
            "use_multiscale_reid": False,
            "use_gnn_matching": False,
        }
    )
    topology = {
        "num_cameras": 2,
        "camera_adjacency": {
            ("cam-1", "cam-2"): 8.0,
            ("cam-2", "cam-1"): 8.0,
        },
    }
    facade = UltimateAdapterCoreFacade(
        session_id="session-1",
        camera_id="cam-1",
        topology=topology,
        config=cfg,
        detector=object(),
        tracker_backend=_FakeTrackerBackend(),
        feature_extractor=_FakeFeatureExtractor(),
    )

    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    detections = np.asarray([[2.0, 2.0, 18.0, 20.0, 0.95, 0.0]], dtype=np.float32)

    result = facade.process_frame(frame, detections=detections)

    assert result.session_id == "session-1"
    assert result.camera_id == "cam-1"
    assert result.frame_index == 1
    assert result.births == 1
    assert result.active_identities == 1
    assert result.total_identities == 1
    assert len(result.updates) == 1

    update = result.updates[0]
    assert update.track["track_id"] == 11
    assert update.track["bbox"] == {"x1": 2, "y1": 2, "x2": 18, "y2": 20}
    assert update.identity["global_id"] == 1
    assert update.identity["lifecycle"] == "new"
    assert update.identity["stage"] == "NEW-BORN"

    assert facade.topology["num_cameras"] == 2
    assert facade.bundle.tracker.cfg["num_cameras"] == 2
    assert facade.bundle.tracker.cfg["camera_adjacency"] == topology["camera_adjacency"]
    assert facade.bundle.registry.get_total_count() == 1
    assert facade.bundle.registry.get_active_count() == 1

    cleanup = facade.cleanup()
    assert cleanup.session_id == "session-1"
    assert cleanup.camera_id == "cam-1"
    assert cleanup.released_tracks == 1
    assert cleanup.active_tracks == 0
    assert cleanup.total_identities == 1

    facade.shutdown()


def test_facade_shutdown_blocks_future_updates(tmp_path):
    cfg = UltimateCoreConfig(
        overrides={
            "device": "cpu",
            "embedding_storage_dir": str(tmp_path),
            "birth_min_frames": 1,
            "birth_min_conf": 0.0,
            "use_ukf": False,
            "use_transformer_motion": False,
            "use_temporal_consistency": False,
            "use_multiscale_reid": False,
            "use_gnn_matching": False,
        }
    )
    facade = UltimateAdapterCoreFacade(
        session_id="session-2",
        camera_id="cam-2",
        topology={"num_cameras": 1},
        config=cfg,
        detector=object(),
        tracker_backend=_FakeTrackerBackend(),
        feature_extractor=_FakeFeatureExtractor(),
    )

    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    detections = np.asarray([[2.0, 2.0, 18.0, 20.0, 0.95, 0.0]], dtype=np.float32)

    facade.process_frame(frame, detections=detections)
    shutdown = facade.shutdown()

    assert shutdown.closed is True
    assert shutdown.active_tracks == 0
    assert shutdown.total_identities == 1

    with pytest.raises(RuntimeError):
        facade.process_frame(frame, detections=detections)


class _FakeTrackerBackend:
    def update(self, detections, frame):
        return np.asarray([[2.0, 2.0, 18.0, 20.0, 11, 0.95, 0]], dtype=np.float32)


class _FakeFeatureExtractor:
    def extract_batch(self, frame, boxes, confs=None):
        outputs = []
        for idx, _bbox in enumerate(boxes):
            outputs.append((_make_embedding(100.0 + idx), _make_embedding(200.0 + idx)))
        return outputs
