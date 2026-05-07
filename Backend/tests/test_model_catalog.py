"""Regression tests for the V2 production model catalog."""

from __future__ import annotations

import json
from pathlib import Path


def test_model_catalog_declares_required_supported_models():
    catalog_path = (
        Path(__file__).resolve().parent.parent
        / "services"
        / "triton"
        / "models"
        / "catalog.json"
    )

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    models = {model["id"]: model for model in catalog["models"]}

    assert catalog["catalog_version"] == 1
    assert set(models) >= {"yolo26", "phone_detection", "yolo26-pose", "scrfd", "arcface", "osnet"}

    assert models["yolo26"]["runtime_model_name"] == "yolo26"
    assert models["phone_detection"]["runtime_model_name"] == "phone_detection"
    assert models["yolo26-pose"]["runtime_model_name"] == "yolo26-pose"
    assert models["scrfd"]["runtime_model_name"] == "scrfd"
    assert models["arcface"]["runtime_model_name"] == "arcface"
    assert models["osnet"]["runtime_model_name"] == "osnet"

    assert models["yolo26"]["artifact"]["required_file"] == "model.plan"
    assert models["yolo26"]["artifact"]["repository_path"] == "v2/services/triton/models/yolo26/1"
    assert models["phone_detection"]["artifact"]["required_file"] == "model.plan"
    assert models["phone_detection"]["artifact"]["repository_path"] == (
        "v2/services/triton/models/phone_detection/1"
    )
    assert models["yolo26-pose"]["artifact"]["required_file"] == "model.plan"
    assert models["yolo26-pose"]["artifact"]["repository_path"] == (
        "v2/services/triton/models/yolo26-pose/1"
    )
    assert models["scrfd"]["artifact"]["required_file"] == "model.plan"
    assert models["arcface"]["artifact"]["required_file"] == "model.plan"


def test_model_catalog_marks_missing_local_sources_explicitly():
    catalog_path = (
        Path(__file__).resolve().parent.parent
        / "services"
        / "triton"
        / "models"
        / "catalog.json"
    )

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    models = {model["id"]: model for model in catalog["models"]}

    assert models["yolo26"]["source"]["status"] == "available-in-repo"
    assert models["phone_detection"]["source"]["status"] == "available-in-repo"
    assert models["yolo26-pose"]["source"]["status"] == "available-in-repo"
    assert models["scrfd"]["source"]["status"] == "export-at-build-time"
    assert models["arcface"]["source"]["status"] == "export-at-build-time"
    assert models["arcface"]["source"]["reference"] == "insightface buffalo_l / w600k_r50.onnx"
    assert models["osnet"]["source"]["status"] == "export-at-build-time"
