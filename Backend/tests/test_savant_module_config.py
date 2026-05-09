"""Regression tests for Savant module configuration shape."""

from pathlib import Path

import yaml


def test_savant_module_uses_pyfunc_element_for_redis_publishing_and_real_sink():
    module_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.yml"
    )
    config = yaml.safe_load(module_yml.read_text(encoding="utf-8"))

    elements = config["pipeline"]["elements"]
    sink = config["pipeline"]["sink"]

    assert any(
        element.get("element") == "pyfunc" and element.get("module") == "redis_sink"
        for element in elements
    )
    assert sink == [{"element": "devnull_sink"}]


def test_savant_module_uses_omegaconf_env_resolvers_instead_of_shell_defaults():
    module_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.yml"
    )
    text = module_yml.read_text(encoding="utf-8")

    assert "${RTSP_URI:-" not in text
    assert "${REDIS_URL:-" not in text
    assert "${oc.env:REDIS_URL," in text
    assert "${oc.env:ZMQ_SRC_ENDPOINT}" in text


def test_savant_module_uses_model_blocks_for_detector_elements():
    module_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.yml"
    )
    config = yaml.safe_load(module_yml.read_text(encoding="utf-8"))

    detector_elements = [
        element for element in config["pipeline"]["elements"]
        if isinstance(element.get("element"), str) and element["element"].startswith("nvinfer@")
    ]

    assert {element["name"] for element in detector_elements} >= {"person_detector", "phone_detector"}

    person = next(element for element in detector_elements if element["name"] == "person_detector")
    phone = next(element for element in detector_elements if element["name"] == "phone_detector")

    assert person["element"] == "nvinfer@detector"
    assert person["model"]["local_path"] == "/models/yolo26/1"
    assert person["model"]["model_file"] == "yolo26s.onnx"
    assert person["model"]["input"]["scale_factor"] == 0.00392156862745098
    assert person["model"]["output"]["layer_names"] == ["output0"]
    assert person["model"]["output"]["num_detected_classes"] == 80

    assert phone["element"] == "nvinfer@detector"
    assert phone["model"]["local_path"] == "/models/phone_detection/1"
    assert phone["model"]["model_file"] == "best.onnx"
    assert phone["model"]["input"]["scale_factor"] == 0.00392156862745098
    assert phone["model"]["input"]["object"] == "person_detector.person"


def test_full_savant_module_uses_zeromq_source_for_multi_camera_ingest():
    module_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.yml"
    )
    config = yaml.safe_load(module_yml.read_text(encoding="utf-8"))

    source = config["pipeline"]["source"]

    assert source["element"] == "zeromq_source_bin"
    assert source["properties"]["socket"] == "${oc.env:ZMQ_SRC_ENDPOINT}"
    assert "uri" not in source["properties"]


def test_local_savant_module_is_person_only_and_keeps_redis_sink():
    module_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.local.yml"
    )
    config = yaml.safe_load(module_yml.read_text(encoding="utf-8"))

    elements = config["pipeline"]["elements"]
    detector_elements = [
        element for element in elements
        if isinstance(element.get("element"), str) and element["element"].startswith("nvinfer@")
    ]

    assert [element["name"] for element in detector_elements] == ["person_detector"]
    assert detector_elements[0]["model"]["local_path"] == "/models/yolo26/1"
    assert detector_elements[0]["model"]["model_file"] == "yolo26s.onnx"
    assert detector_elements[0]["model"]["input"]["color_format"] == "rgb"
    assert detector_elements[0]["model"]["input"]["scale_factor"] == 0.00392156862745098
    assert any(
        element.get("element") == "pyfunc" and element.get("module") == "redis_sink"
        for element in elements
    )


def test_tracker_config_uses_non_unique_ids_for_backend_stable_track_keys():
    tracker_yml = (
        Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "config" / "tracker.yml"
    )
    text = tracker_yml.read_text(encoding="utf-8")

    assert "useUniqueID: 0" in text
