"""Regression tests for Savant runtime compose wiring."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_local_gpu_full_compose_mounts_full_savant_module_and_model_repo():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    savant = compose["services"]["savant-pipeline"]
    volumes = savant["volumes"]

    assert "./services/savant-pipeline/models:/models" in volumes
    assert "./services/savant-pipeline/module.local.yml:/opt/savant/module/module.yml:ro" not in volumes
    assert savant["profiles"] == ["gpu-full"]
    assert savant["depends_on"]["session-control"]["condition"] == "service_healthy"
    assert "RTSP_URI" not in savant["environment"]
    assert savant["environment"]["ZMQ_SRC_ENDPOINT"] == "router+bind:tcp://0.0.0.0:5000"


def test_local_gpu_lite_compose_mounts_lite_savant_module_override():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    savant = compose["services"]["savant-pipeline-lite"]
    volumes = savant["volumes"]

    assert "./services/savant-pipeline/models:/models" in volumes
    assert "./services/savant-pipeline/module.local.yml:/opt/savant/module/module.yml:ro" in volumes
    assert savant["profiles"] == ["gpu"]
    assert savant["depends_on"]["session-control"]["condition"] == "service_healthy"
    assert "RTSP_URI" not in savant["environment"]


def test_local_compose_moves_triton_and_identity_to_full_gpu_profile():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    assert compose["services"]["triton"]["profiles"] == ["gpu-full"]
    assert compose["services"]["identity-consumer"]["profiles"] == ["gpu-full"]
    command = compose["services"]["triton"]["command"]
    assert "--model-control-mode=explicit" in command
    assert "--load-model=arcface" in command
    assert "--load-model=osnet" in command


def test_local_compose_adds_session_control_bridge():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    session_control = compose["services"]["session-control"]

    assert session_control["profiles"] == ["gpu", "gpu-full"]
    assert session_control["environment"]["GO2RTC_URL"] == "http://go2rtc:1984"
    assert session_control["depends_on"]["go2rtc"]["condition"] == "service_started"
    assert session_control["depends_on"]["api"]["condition"] == "service_started"


def test_local_compose_adds_ultimate_adapter_service():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    adapter = compose["services"]["ultimate-adapter"]

    assert adapter["profiles"] == ["gpu", "gpu-full"]
    assert adapter["environment"]["GO2RTC_RTSP_BASE_URL"] == "rtsp://go2rtc:8556"
    assert adapter["depends_on"]["session-control"]["condition"] == "service_healthy"
    assert adapter["depends_on"]["api"]["condition"] == "service_started"


def test_main_compose_adds_ultimate_adapter_service():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    adapter = compose["services"]["ultimate-adapter"]

    assert adapter["profiles"] == ["gpu"]
    assert adapter["environment"]["GO2RTC_RTSP_BASE_URL"] == "rtsp://go2rtc:8555"
    assert adapter["depends_on"]["redis"]["condition"] == "service_healthy"
    assert adapter["depends_on"]["go2rtc"]["condition"] == "service_started"
    assert adapter["depends_on"]["api"]["condition"] == "service_started"


def test_local_gpu_full_compose_adds_multi_camera_savant_feeder():
    compose_path = Path(__file__).resolve().parent.parent / "docker-compose.local.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    feeder = compose["services"]["savant-feeder"]

    assert feeder["profiles"] == ["gpu", "gpu-full"]
    assert feeder["environment"]["ZMQ_SRC_ENDPOINT"] == "dealer+connect:tcp://savant-pipeline:5000"
    assert feeder["depends_on"]["session-control"]["condition"] == "service_healthy"
    assert feeder["depends_on"]["savant-pipeline"]["condition"] == "service_started"
