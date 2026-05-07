"""Regression tests for the model export builder contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


def test_export_builder_dockerfile_exists_and_uses_tensorrt_base():
    dockerfile = Path(__file__).resolve().parent.parent / "docker" / "export-models.Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert "FROM nvcr.io/nvidia/tensorrt:" in text
    assert "ultralytics" in text
    assert "torchreid" in text
    assert "tensorboard" in text
    assert "insightface" in text


def test_export_builder_wrapper_mounts_repo_and_runs_export_script():
    script = Path(__file__).resolve().parent.parent / "scripts" / "export_tensorrt_in_docker.sh"
    text = script.read_text(encoding="utf-8")

    assert "docker build" in text
    assert "docker run" in text
    assert "/workspace" in text
    assert "python v2/scripts/export_tensorrt.py" in text


def test_local_gpu_bootstrap_invokes_model_preflight():
    local_script = Path(__file__).resolve().parent.parent / "local.sh"
    helper_script = Path(__file__).resolve().parent.parent / "scripts" / "ensure_local_triton_models.sh"
    savant_helper_script = (
        Path(__file__).resolve().parent.parent / "scripts" / "ensure_local_savant_models.sh"
    )

    local_text = local_script.read_text(encoding="utf-8")
    helper_text = helper_script.read_text(encoding="utf-8")
    savant_helper_text = savant_helper_script.read_text(encoding="utf-8")

    assert "./local.sh --gpu" not in helper_text
    assert "ensure_local_triton_models.sh" in local_text
    assert "ensure_local_savant_models.sh" in local_text
    assert "./local.sh --gpu" not in helper_text
    assert "ensure_local_triton_models.sh" in local_text
    assert "ensure_local_savant_models.sh" in local_text
    assert "--model \"$model\"" in helper_text
    assert "\"yolo26\"" in helper_text
    assert "\"phone_detection\"" in helper_text
    assert "\"yolo26-pose\"" in helper_text
    assert "\"arcface\"" in helper_text
    assert "\"osnet\"" in helper_text
    assert "gpu-full-ready" in helper_text
    assert "\"prepare_phone_detection_onnx\"" in savant_helper_text
    assert "\"prepare_yolo26_onnx\"" in savant_helper_text
    assert "\"prepare_yolo26_pose_onnx\"" in savant_helper_text
    assert "services/savant-pipeline/models" in savant_helper_text


def test_phone_export_uses_trtexec_without_explicit_shapes_for_static_onnx(tmp_path, monkeypatch):
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "export_tensorrt.py"
    spec = importlib.util.spec_from_file_location("export_tensorrt_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    phone_source = tmp_path / "best.pt"
    phone_source.write_text("weights", encoding="utf-8")
    phone_onnx = tmp_path / "best.onnx"
    phone_onnx.write_text("onnx", encoding="utf-8")
    models_dir = tmp_path / "models"

    monkeypatch.setattr(module, "PHONE_MODEL_SOURCE", phone_source)
    monkeypatch.setattr(module, "PHONE_ONNX_SOURCE", phone_onnx)
    monkeypatch.setattr(module, "MODELS_DIR", models_dir)
    monkeypatch.setattr(module, "export_onnx_from_pt_source", lambda *_args, **_kwargs: phone_onnx)

    fake_ultralytics = SimpleNamespace(YOLO=lambda *_args, **_kwargs: SimpleNamespace(export=lambda **_kw: None))
    monkeypatch.setitem(sys.modules, "ultralytics", fake_ultralytics)

    calls: list[list[str]] = []

    def fake_run(args: list[str], check: bool):
        assert check is True
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.export_phone_detection()

    assert len(calls) == 1
    assert all(
        not arg.startswith(("--minShapes=", "--optShapes=", "--maxShapes="))
        for arg in calls[0]
    )


def test_arcface_source_refreshes_invalid_repo_artifact_from_insightface_cache(tmp_path, monkeypatch):
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "export_tensorrt.py"
    spec = importlib.util.spec_from_file_location("export_tensorrt_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    repo_arcface = tmp_path / "backend" / "models" / "arcface_r100.onnx"
    repo_arcface.parent.mkdir(parents=True, exist_ok=True)
    repo_arcface.write_text("invalid", encoding="utf-8")

    insightface_arcface = tmp_path / ".insightface" / "models" / "buffalo_l" / "w600k_r50.onnx"
    insightface_arcface.parent.mkdir(parents=True, exist_ok=True)
    insightface_arcface.write_bytes(b"valid-onnx")

    monkeypatch.setattr(module, "ARCFACE_ONNX_SOURCE", repo_arcface)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)

    monkeypatch.setattr(
        module,
        "is_valid_onnx_model",
        lambda path: Path(path).exists() and Path(path).read_bytes() == b"valid-onnx",
    )

    prepared: dict[str, object] = {}

    class FakeFaceAnalysis:
        def __init__(self, name: str, providers: list[str]):
            prepared["name"] = name
            prepared["providers"] = providers

        def prepare(self, ctx_id: int, det_size: tuple[int, int]):
            prepared["ctx_id"] = ctx_id
            prepared["det_size"] = det_size

    monkeypatch.setitem(sys.modules, "insightface", types.ModuleType("insightface"))
    monkeypatch.setitem(sys.modules, "insightface.app", types.SimpleNamespace(FaceAnalysis=FakeFaceAnalysis))

    normalized: dict[str, object] = {}

    def fake_normalize(path: Path, input_name: str, output_name: str) -> Path:
        normalized["path"] = path
        normalized["input_name"] = input_name
        normalized["output_name"] = output_name
        return path

    monkeypatch.setattr(module, "ensure_canonical_onnx_io_names", fake_normalize)

    resolved = module.ensure_arcface_source()

    assert resolved == repo_arcface
    assert repo_arcface.read_bytes() == b"valid-onnx"
    assert normalized == {
        "path": repo_arcface,
        "input_name": "input",
        "output_name": "output",
    }
    assert prepared == {
        "name": "buffalo_l",
        "providers": ["CPUExecutionProvider"],
        "ctx_id": 0,
        "det_size": (640, 640),
    }


def test_osnet_export_uses_legacy_torch_onnx_exporter(tmp_path, monkeypatch):
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "export_tensorrt.py"
    spec = importlib.util.spec_from_file_location("export_tensorrt_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    module.MODELS_DIR = tmp_path / "models"

    export_kwargs: dict[str, object] = {}

    class FakeModel:
        def eval(self):
            return None

    fake_torch = types.SimpleNamespace(
        randn=lambda *args, **kwargs: "dummy-input",
        onnx=types.SimpleNamespace(
            export=lambda model, dummy, onnx_path, **kwargs: export_kwargs.update(kwargs)
        ),
    )
    fake_torchreid = types.SimpleNamespace(
        models=types.SimpleNamespace(
            build_model=lambda **kwargs: FakeModel()
        )
    )

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchreid", fake_torchreid)

    calls: list[list[str]] = []

    def fake_run(args: list[str], check: bool):
        assert check is True
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.export_osnet()

    assert export_kwargs["dynamo"] is False
    assert len(calls) == 1


def test_prepare_phone_detection_onnx_exports_from_pt_source(tmp_path, monkeypatch):
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "export_tensorrt.py"
    spec = importlib.util.spec_from_file_location("export_tensorrt_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    phone_source = tmp_path / "best.pt"
    phone_source.write_text("weights", encoding="utf-8")
    savant_models_dir = tmp_path / "savant-models"
    phone_onnx = tmp_path / "best.onnx"

    monkeypatch.setattr(module, "PHONE_MODEL_SOURCE", phone_source)
    monkeypatch.setattr(module, "PHONE_ONNX_SOURCE", phone_onnx)
    monkeypatch.setattr(module, "SAVANT_MODELS_DIR", savant_models_dir)
    monkeypatch.setattr(module, "export_onnx_from_pt_source", lambda *_args, **_kwargs: phone_onnx)

    class FakeYOLO:
        def __init__(self, model_path: str):
            self.model_path = Path(model_path)

        def export(self, **_kwargs):
            self.model_path.with_suffix(".onnx").write_bytes(b"phone-onnx")

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

    phone_onnx = tmp_path / "best.onnx"
    monkeypatch.setattr(module, "PHONE_ONNX_SOURCE", phone_onnx)
    monkeypatch.setattr(module, "export_onnx_from_pt_source", lambda *_args, **_kwargs: phone_onnx)

    module.prepare_phone_detection_onnx()

    exported = savant_models_dir / "phone_detection" / "1" / "best.onnx"
    assert exported.read_bytes() == b"phone-onnx"
