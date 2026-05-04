from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


DEFAULT_ULTIMATE_CORE_CONFIG: dict[str, Any] = {
    "det_model": "yolo11s.pt",
    "det_conf": 0.30,
    "det_iou": 0.35,
    "det_classes": [0],
    "det_imgsz": 960,
    "reid_model": "osnet_x0_25_msmt17.pt",
    "ema_alpha": 0.35,
    "min_hits": 1,
    "max_age": 60,
    "bank_max_embeds": 25,
    "direct_match_thr_same": 0.65,
    "direct_match_thr_cross": 0.58,
    "bank_cosine_thr_same": 0.54,
    "bank_cosine_thr_cross": 0.48,
    "lock_cosine_thr": 0.42,
    "hungarian_match_thr": 0.42,
    "ambiguous_margin": 0.06,
    "active_identity_penalty": 0.05,
    "motion_sigma_px": 28.0,
    "motion_weight": 0.24,
    "iou_weight": 0.20,
    "temporal_weight": 0.14,
    "temporal_decay_frames": 20,
    "lock_after_frames": 4,
    "enable_frame_skip": False,
    "frame_skip_interval": 2,
    "stage1_iou_thr": 0.25,
    "stage1_max_gap": 15,
    "stage2_cos_thr": 0.55,
    "stage3_seed_thr": 0.48,
    "birth_min_frames": 2,
    "birth_min_conf": 0.40,
    "birth_max_frames": 10,
    "dup_birth_block_thr": 0.48,
    "dup_birth_margin": 0.08,
    "occlusion_max_frames": 300,
    "deep_feature_weight": 0.70,
    "color_feature_weight": 0.20,
    "use_multiscale_reid": False,
    "pyramid_scales": [1.0, 0.75, 0.5],
    "use_ukf": True,
    "ukf_alpha": 1e-3,
    "ukf_beta": 2.0,
    "ukf_kappa": 0.0,
    "ukf_process_noise": 0.03,
    "ukf_measurement_noise": 0.2,
    "use_transformer_motion": False,
    "transformer_d_model": 128,
    "transformer_nhead": 4,
    "transformer_num_layers": 2,
    "transformer_model_path": None,
    "use_temporal_consistency": False,
    "temporal_history_len": 8,
    "temporal_change_threshold": 0.7,
    "use_gnn_matching": False,
    "num_cameras": 6,
    "camera_transition_sigma": 30.0,
    "camera_adjacency": {
        ("1", "2"): 8.0,
        ("2", "1"): 8.0,
        ("2", "3"): 12.0,
        ("3", "2"): 12.0,
        ("1", "3"): 20.0,
        ("3", "1"): 20.0,
        ("3", "4"): 10.0,
        ("4", "3"): 10.0,
        ("4", "5"): 10.0,
        ("5", "4"): 10.0,
        ("5", "6"): 10.0,
        ("6", "5"): 10.0,
    },
    "cross_camera_tau_sec": 60.0,
    "min_travel_time": 2.0,
    "skip_frames": 2,
    "fp16": False,
    "device": "cpu",
    "debug_overlay": False,
    "feature_cache_max_size": 256,
    "max_match_identities": 256,
    "trail_length": 60,
    "identity_prune_seconds": 7 * 24 * 3600,
    "recent_birth_merge_frames": 10,
    "recent_birth_merge_thr": 0.75,
    "embedding_storage_dir": None,
}


@dataclass(frozen=True)
class UltimateCoreConfig:
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cfg = dict(DEFAULT_ULTIMATE_CORE_CONFIG)
        cfg.update(dict(self.overrides))
        if cfg["embedding_storage_dir"] is None:
            cfg["embedding_storage_dir"] = None
        return cfg

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | "UltimateCoreConfig") -> "UltimateCoreConfig":
        if isinstance(mapping, UltimateCoreConfig):
            return mapping
        return cls(overrides=dict(mapping))


def coerce_core_config(cfg: Mapping[str, Any] | UltimateCoreConfig) -> dict[str, Any]:
    if isinstance(cfg, UltimateCoreConfig):
        return cfg.to_dict()
    return dict(UltimateCoreConfig.from_mapping(cfg).to_dict())
