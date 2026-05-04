from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
import time

import numpy as np

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from ultimate_adapter.ultimate_core.registry import GlobalIdentityRegistry  # noqa: E402


def test_active_identity_on_another_camera_is_not_reused_for_cross_camera_match(tmp_path):
    registry = GlobalIdentityRegistry(
        {
            "embedding_storage_dir": str(tmp_path),
            "min_travel_time": 0.0,
            "use_gnn_matching": False,
            "use_transformer_motion": False,
            "use_temporal_consistency": False,
            "use_multiscale_reid": False,
        }
    )

    embedding = np.ones(512, dtype=np.float32)
    color = np.ones(32, dtype=np.float32)
    bbox = (10, 10, 50, 90)
    global_id = registry.create_identity(embedding, color, bbox, frame_idx=1, camera_id="cam-1")
    registry.assign_track(global_id, 11, "cam-1")

    identity = registry.get_identity(global_id)
    assert identity is not None
    identity.last_seen_time = time.time() - 10.0
    identity.viewpoint_bank = deque(identity.viewpoint_bank, maxlen=50)

    score, *_ = registry._score_identity(
        identity,
        {"embedding": embedding, "color": color, "bbox": bbox},
        frame_idx=2,
        obs_camera="cam-2",
    )

    assert score < 0.0
    registry.close()
