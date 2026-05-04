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
    assert set(models) >= {"yolov8_person", "yolov8_phone", "arcface", "osnet"}

    assert models["yolov8_person"]["runtime_model_name"] == "yolov8_person"
    assert models["yolov8_phone"]["runtime_model_name"] == "yolov8_phone"
    assert models["arcface"]["runtime_model_name"] == "arcface"
    assert models["osnet"]["runtime_model_name"] == "osnet"

    assert models["yolov8_person"]["artifact"]["required_file"] == "yolov8n.onnx"
    assert models["yolov8_person"]["artifact"]["repository_path"] == (
        "v2/services/savant-pipeline/models/yolov8_person/1"
    )
    assert models["yolov8_phone"]["artifact"]["required_file"] == "best.onnx"
    assert models["yolov8_phone"]["artifact"]["repository_path"] == (
        "v2/services/savant-pipeline/models/yolov8_phone/1"
    )
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

    assert models["yolov8_person"]["source"]["status"] == "external-download-required"
    assert models["yolov8_phone"]["source"]["status"] == "available-in-repo"
    assert models["arcface"]["source"]["status"] == "export-at-build-time"
    assert models["arcface"]["source"]["reference"] == "insightface buffalo_l / w600k_r50.onnx"
    assert models["osnet"]["source"]["status"] == "export-at-build-time"
