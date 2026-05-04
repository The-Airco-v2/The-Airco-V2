from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .config import UltimateCoreConfig
from .registry import GlobalIdentityRegistry
from .tracker import UltimateStableTrackerV2


@dataclass
class UltimateCoreBundle:
    config: UltimateCoreConfig
    registry: GlobalIdentityRegistry
    tracker: UltimateStableTrackerV2

    def close(self) -> None:
        self.registry.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def build_ultimate_core_bundle(
    config: Mapping[str, Any] | UltimateCoreConfig,
    camera_id: str = "1",
    *,
    detector=None,
    tracker_backend=None,
    feature_extractor=None,
    registry: Optional[GlobalIdentityRegistry] = None,
) -> UltimateCoreBundle:
    cfg = UltimateCoreConfig.from_mapping(config)
    cfg_dict = cfg.to_dict()
    registry_obj = registry or GlobalIdentityRegistry(cfg_dict)
    tracker = UltimateStableTrackerV2(
        cfg_dict,
        camera_id=camera_id,
        detector=detector,
        tracker_backend=tracker_backend,
        feature_extractor=feature_extractor,
        registry=registry_obj,
    )
    return UltimateCoreBundle(config=cfg, registry=registry_obj, tracker=tracker)

