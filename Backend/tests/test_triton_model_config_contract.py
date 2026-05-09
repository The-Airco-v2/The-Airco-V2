"""Regression tests for Triton identity model config compatibility."""

from pathlib import Path


def test_identity_model_configs_match_batch1_engines():
    models_dir = Path(__file__).resolve().parent.parent / "services" / "triton" / "models"

    expected_batch = {"arcface": 32, "osnet": 64}
    for model in ("arcface", "osnet"):
        config_text = (models_dir / model / "config.pbtxt").read_text(encoding="utf-8")
        assert f"max_batch_size: {expected_batch[model]}" in config_text
        assert "dynamic_batching" in config_text


def test_detector_model_configs_exist_for_new_model_family():
    models_dir = Path(__file__).resolve().parent.parent / "services" / "triton" / "models"

    for model, expected_name in (
        ("yolo26", "yolo26"),
        ("phone_detection", "phone_detection"),
        ("yolo26-pose", "yolo26-pose"),
    ):
        config_text = (models_dir / model / "config.pbtxt").read_text(encoding="utf-8")
        assert f'name: "{expected_name}"' in config_text
