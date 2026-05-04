"""Adapter settings and canonical selector helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTROL_STREAM = "airco:control"
CONTROL_GROUP = "ultimate-adapter-group"

STANDARD_SELECTOR = "standard"
ULTIMATE_SELECTOR = "ultimate"
LEGACY_ULTIMATE_SELECTOR = "ultimate_reid"

ACTIVE_SESSION_KEY = "airco:ultimate-adapter:active_session_id"
ACTIVE_CAMERA_IDS_KEY = "airco:ultimate-adapter:active_camera_ids"
ACTIVE_SELECTOR_KEY = "airco:ultimate-adapter:active_selector"
SESSION_ALIAS_CONTRACT_KEY = "airco:ultimate-adapter:session_alias_contracts"
SESSION_PERSON_MAP_KEY_PREFIX = "airco:ultimate-adapter:session-person-map"
RUNTIME_STATUS_KEY = "airco:ultimate-adapter:runtime_status"


@dataclass(frozen=True)
class UltimateAdapterSettings:
    """Runtime settings for the adapter shell."""

    go2rtc_rtsp_base_url: str = "rtsp://host.docker.internal:8556"
    control_stream: str = CONTROL_STREAM
    control_group: str = CONTROL_GROUP
    active_session_key: str = ACTIVE_SESSION_KEY
    active_camera_ids_key: str = ACTIVE_CAMERA_IDS_KEY
    active_selector_key: str = ACTIVE_SELECTOR_KEY
    session_alias_contract_key: str = SESSION_ALIAS_CONTRACT_KEY
    session_person_map_key_prefix: str = SESSION_PERSON_MAP_KEY_PREFIX
    runtime_status_key: str = RUNTIME_STATUS_KEY
    runtime_poll_interval_seconds: float = 1.0
    runtime_fps: int = 8
    runtime_reconnect_delay_seconds: float = 1.0
    snapshot_interval_frames: int = 30
    ultimate_device: str = "cuda:0"
    embedding_storage_dir: str | None = None
    det_model_path: str | None = None
    reid_model_path: str | None = None


def load_settings() -> UltimateAdapterSettings:
    """Read adapter settings from the environment."""

    import os

    return UltimateAdapterSettings(
        go2rtc_rtsp_base_url=os.getenv("GO2RTC_RTSP_BASE_URL", "rtsp://host.docker.internal:8556"),
        runtime_poll_interval_seconds=float(os.getenv("ULTIMATE_RUNTIME_POLL_INTERVAL_SECONDS", "1.0")),
        runtime_fps=int(os.getenv("ULTIMATE_RUNTIME_FPS", "8")),
        runtime_reconnect_delay_seconds=float(os.getenv("ULTIMATE_RUNTIME_RECONNECT_DELAY_SECONDS", "1.0")),
        snapshot_interval_frames=int(os.getenv("ULTIMATE_SNAPSHOT_INTERVAL_FRAMES", "30")),
        ultimate_device=os.getenv("ULTIMATE_DEVICE", "cuda:0"),
        embedding_storage_dir=os.getenv("ULTIMATE_EMBEDDING_STORAGE_DIR") or None,
        det_model_path=os.getenv("ULTIMATE_DET_MODEL_PATH") or None,
        reid_model_path=os.getenv("ULTIMATE_REID_MODEL_PATH") or None,
    )


def normalize_selector(value: Any) -> str:
    """Map control-path selector values to canonical adapter selectors."""

    if not isinstance(value, str):
        return STANDARD_SELECTOR

    normalized = value.strip().lower()
    if normalized == LEGACY_ULTIMATE_SELECTOR:
        return ULTIMATE_SELECTOR
    if normalized in {STANDARD_SELECTOR, ULTIMATE_SELECTOR}:
        return normalized
    return STANDARD_SELECTOR


def selector_from_fields(fields: dict[str, Any]) -> str:
    """Extract the canonical selector from a control event payload."""

    return normalize_selector(fields.get("reid_profile"))


def session_person_map_key(session_id: Any) -> str:
    """Redis hash key for Ultimate global-id to canonical SessionPerson mapping."""

    return f"{SESSION_PERSON_MAP_KEY_PREFIX}:{session_id}"
