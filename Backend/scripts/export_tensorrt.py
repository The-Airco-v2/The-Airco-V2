"""Prepare canonical model artifacts for the local V2 inference stack.

Usage:
    python scripts/export_tensorrt.py --model prepare_savant_person_onnx
    python scripts/export_tensorrt.py --model prepare_savant_phone_onnx
    python scripts/export_tensorrt.py --model arcface
    python scripts/export_tensorrt.py --model osnet
    python scripts/export_tensorrt.py --all

Requires: ultralytics, insightface, torchreid, tensorrt
Run on the GPU server (Utho) where TensorRT is available.
"""

import argparse
import subprocess
from pathlib import Path
import shutil
import tempfile

REPO_ROOT = Path(__file__).parent.parent.parent
MODELS_DIR = Path(__file__).parent.parent / "services" / "triton" / "models"
SAVANT_MODELS_DIR = Path(__file__).parent.parent / "services" / "savant-pipeline" / "models"
PHONE_MODEL_SOURCE = REPO_ROOT / "models" / "phone_usage_detection" / "best.pt"
ARCFACE_ONNX_SOURCE = REPO_ROOT / "backend" / "models" / "arcface_r100.onnx"


def is_valid_onnx_model(path: Path) -> bool:
    """Return whether the given path contains a parseable ONNX model."""
    if not path.exists():
        return False

    try:
        import onnx

        onnx.load(path)
    except Exception:
        return False

    return True


def ensure_canonical_onnx_io_names(path: Path, input_name: str, output_name: str) -> Path:
    """Rewrite a valid ONNX model in-place to expose canonical input/output tensor names."""
    import onnx

    model = onnx.load(path)
    current_input = model.graph.input[0].name
    current_output = model.graph.output[0].name

    if current_input == input_name and current_output == output_name:
        return path

    if current_input != input_name:
        model.graph.input[0].name = input_name
        for node in model.graph.node:
            node.input[:] = [input_name if name == current_input else name for name in node.input]

    if current_output != output_name:
        model.graph.output[0].name = output_name
        for node in model.graph.node:
            node.output[:] = [output_name if name == current_output else name for name in node.output]

    onnx.save(model, path)
    return path


def ensure_arcface_source() -> Path:
    """Ensure a valid ArcFace ONNX source artifact is available for export."""
    if is_valid_onnx_model(ARCFACE_ONNX_SOURCE):
        return ensure_canonical_onnx_io_names(ARCFACE_ONNX_SOURCE, "input", "output")

    from insightface.app import FaceAnalysis

    print("Refreshing ArcFace source from InsightFace buffalo_l cache...")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    arcface_src = Path.home() / ".insightface" / "models" / "buffalo_l" / "w600k_r50.onnx"
    if not arcface_src.exists():
        raise FileNotFoundError(
            f"InsightFace ArcFace source not found after download step: {arcface_src}"
        )
    if not is_valid_onnx_model(arcface_src):
        raise ValueError(f"InsightFace ArcFace source is not a valid ONNX model: {arcface_src}")

    ARCFACE_ONNX_SOURCE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(arcface_src, ARCFACE_ONNX_SOURCE)
    if not is_valid_onnx_model(ARCFACE_ONNX_SOURCE):
        raise ValueError(
            f"Refreshed ArcFace source is still not a valid ONNX model: {ARCFACE_ONNX_SOURCE}"
        )

    return ensure_canonical_onnx_io_names(ARCFACE_ONNX_SOURCE, "input", "output")


def export_yolov8_person():
    """Export YOLOv8n person detection to TensorRT."""
    print("Exporting YOLOv8n person detection...")
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    model.export(
        format="engine",
        imgsz=640,
        half=True,
        batch=16,
        device=0,
    )
    dest = MODELS_DIR / "yolov8_person" / "1" / "model.plan"
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path("yolov8n.engine").rename(dest)
    print(f"Saved to {dest}")


def prepare_savant_person_onnx():
    """Export the canonical person detector to a Savant-owned ONNX artifact."""
    print("Preparing Savant person detector ONNX...")
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    model.export(
        format="onnx",
        imgsz=640,
        batch=1,
        device="cpu",
    )
    dest = SAVANT_MODELS_DIR / "yolov8_person" / "1" / "yolov8n.onnx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path("yolov8n.onnx").rename(dest)
    print(f"Saved to {dest}")


def export_yolov8_phone():
    """Export YOLOv8n phone detection to TensorRT."""
    print("Exporting YOLOv8n phone detection...")
    from ultralytics import YOLO

    if not PHONE_MODEL_SOURCE.exists():
        raise FileNotFoundError(f"Phone detector source model not found: {PHONE_MODEL_SOURCE}")

    model = YOLO(str(PHONE_MODEL_SOURCE))
    onnx_path = PHONE_MODEL_SOURCE.with_suffix(".onnx")
    if not onnx_path.exists():
        model.export(
            format="onnx",
            imgsz=640,
            half=True,
            batch=1,
            device="cpu",
        )

    dest = MODELS_DIR / "yolov8_phone" / "1" / "model.plan"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "trtexec",
        f"--onnx={onnx_path}",
        f"--saveEngine={dest}",
        "--fp16",
    ], check=True)
    print(f"Saved to {dest}")


def prepare_savant_phone_onnx():
    """Prepare the Savant-owned ONNX source for the phone detector."""
    print("Preparing Savant phone detector ONNX...")
    if not PHONE_MODEL_SOURCE.exists():
        raise FileNotFoundError(f"Phone detector source model not found: {PHONE_MODEL_SOURCE}")

    source = PHONE_MODEL_SOURCE.with_suffix(".onnx")

    try:
        from ultralytics import YOLO

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_pt = Path(temp_dir) / PHONE_MODEL_SOURCE.name
            shutil.copy2(PHONE_MODEL_SOURCE, temp_pt)
            model = YOLO(str(temp_pt))
            model.export(
                format="onnx",
                imgsz=640,
                batch=1,
                device="cpu",
            )
            exported = temp_pt.with_suffix(".onnx")
            if not exported.exists():
                raise FileNotFoundError(f"Expected exported ONNX artifact was not created: {exported}")
            source = exported
            dest = SAVANT_MODELS_DIR / "yolov8_phone" / "1" / "best.onnx"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            print(f"Saved to {dest}")
            return
    except Exception:
        if not source.exists():
            raise

    dest = SAVANT_MODELS_DIR / "yolov8_phone" / "1" / "best.onnx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    print(f"Saved to {dest}")


def export_scrfd():
    """Download SCRFD (det_10g) ONNX model from insightface buffalo_l for savant-pipeline."""
    print("Preparing SCRFD face detector ONNX...")

    SAVANT_SCRFD = Path(__file__).parent.parent / "services" / "savant-pipeline" / "models" / "scrfd"
    dest = SAVANT_SCRFD / "det_10g.onnx"
    if dest.exists():
        print(f"SCRFD already present at {dest}")
        return

    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    scrfd_src = Path.home() / ".insightface" / "models" / "buffalo_l" / "det_10g.onnx"
    if not scrfd_src.exists():
        raise FileNotFoundError(f"SCRFD det_10g.onnx not found after insightface download: {scrfd_src}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(scrfd_src, dest)
    print(f"Saved to {dest}")


def export_arcface():
    """Export the canonical ArcFace ONNX artifact to TensorRT."""
    print("Exporting ArcFace...")
    arcface_source = ensure_arcface_source()

    dest = MODELS_DIR / "arcface" / "1" / "model.plan"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "trtexec",
        f"--onnx={arcface_source}",
        f"--saveEngine={dest}",
        "--fp16",
        "--minShapes=input:1x3x112x112",
        "--optShapes=input:8x3x112x112",
        "--maxShapes=input:32x3x112x112",
    ], check=True)
    print(f"Saved to {dest}")


def export_osnet():
    """Export OSNet body ReID to ONNX then TensorRT."""
    print("Exporting OSNet (torchreid)...")
    import torch
    import torchreid

    model = torchreid.models.build_model(
        name="osnet_x1_0",
        num_classes=1,
        loss="softmax",
        pretrained=True,
    )
    model.eval()

    dummy = torch.randn(1, 3, 256, 128)
    onnx_path = "/tmp/osnet_x1_0.onnx"
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        dynamo=False,
    )

    dest = MODELS_DIR / "osnet" / "1" / "model.plan"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "trtexec",
        f"--onnx={onnx_path}",
        f"--saveEngine={dest}",
        "--fp16",
        "--minShapes=input:1x3x256x128",
        "--optShapes=input:16x3x256x128",
        "--maxShapes=input:64x3x256x128",
    ], check=True)
    print(f"Saved to {dest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=[
            "yolov8_person",
            "yolov8_phone",
            "arcface",
            "osnet",
            "scrfd",
            "prepare_savant_person_onnx",
            "prepare_savant_phone_onnx",
        ],
    )
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all:
        prepare_savant_person_onnx()
        prepare_savant_phone_onnx()
        export_arcface()
        export_osnet()
        export_scrfd()
    elif args.model:
        {
            "yolov8_person": export_yolov8_person,
            "yolov8_phone": export_yolov8_phone,
            "arcface": export_arcface,
            "osnet": export_osnet,
            "scrfd": export_scrfd,
            "prepare_savant_person_onnx": prepare_savant_person_onnx,
            "prepare_savant_phone_onnx": prepare_savant_phone_onnx,
        }[args.model]()
    else:
        parser.print_help()
