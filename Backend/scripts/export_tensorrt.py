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
import urllib.request

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = BACKEND_ROOT / "services" / "triton" / "models"
SAVANT_MODELS_DIR = BACKEND_ROOT / "services" / "savant-pipeline" / "models"
PHONE_MODEL_SOURCE = BACKEND_ROOT / "Triton Models" / "phone_detections" / "best.pt"
PHONE_ONNX_SOURCE = BACKEND_ROOT / "best.onnx"
POSE_MODEL_SOURCE = BACKEND_ROOT / "Triton Models" / "pose_detetcion" / "yolo26s-pose.pt"
POSE_ONNX_SOURCE = BACKEND_ROOT / "yolo26s-pose.onnx"
ARCFACE_ONNX_SOURCE = BACKEND_ROOT / "backend" / "models" / "arcface_r100.onnx"


def resolve_phone_model_source() -> Path:
    """Return the best available phone detector PT source artifact."""
    if PHONE_MODEL_SOURCE.exists():
        return PHONE_MODEL_SOURCE

    legacy_source = BACKEND_ROOT / "best.pt"
    if legacy_source.exists():
        return legacy_source

    raise FileNotFoundError(
        f"Phone detector source model not found: {PHONE_MODEL_SOURCE} or legacy fallback {legacy_source}"
    )


def resolve_pose_model_source() -> Path:
    """Return the pose detector PT source artifact."""
    if POSE_MODEL_SOURCE.exists():
        return POSE_MODEL_SOURCE

    legacy_source = BACKEND_ROOT / "yolo26s-pose.pt"
    if legacy_source.exists():
        return legacy_source

    raise FileNotFoundError(
        f"Pose detector source model not found: {POSE_MODEL_SOURCE} or legacy fallback {legacy_source}"
    )


def is_valid_onnx_model(path: Path) -> bool:
    """Return whether the given path contains a parseable ONNX model."""
    if not path.exists():
        return False

    try:
        header = path.read_bytes()[:128]
    except Exception:
        return False

    if b"git-lfs.github.com/spec/v1" in header:
        return False

    try:
        import onnx

        onnx.load(path)
    except Exception:
        return False

    return True


def artifact_ready(path: Path) -> bool:
    """Return whether a generated artifact exists and is non-empty."""
    return path.exists() and path.stat().st_size > 0


def onnx_artifact_ready(path: Path) -> bool:
    """Return whether an ONNX artifact exists and parses successfully."""
    return is_valid_onnx_model(path)


def download_file(url: str, dest: Path) -> Path:
    """Download a remote file to a destination path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + ".download")
    with urllib.request.urlopen(url) as response, open(tmp_dest, "wb") as out_file:
        shutil.copyfileobj(response, out_file)
    tmp_dest.replace(dest)
    return dest


def download_file_direct(url: str, dest: Path) -> Path:
    """Download a remote file directly to the destination path.

    This avoids atomic rename issues on mounted Windows paths.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out_file:
        shutil.copyfileobj(response, out_file)
    return dest


def find_insightface_scrfd_onnx(cache_root: Path | None = None) -> Path:
    """Find a valid SCRFD detector ONNX model in the InsightFace buffalo_l cache.

    The cache layout varies between InsightFace versions, so search recursively
    for the first valid ONNX file with a detector-like filename.
    """
    cache_root = cache_root or (Path.home() / ".insightface" / "models" / "buffalo_l")
    if not cache_root.exists():
        raise FileNotFoundError(f"InsightFace cache directory not found: {cache_root}")

    candidates: list[Path] = []
    for candidate in cache_root.rglob("*.onnx"):
        name = candidate.name.lower()
        if "det" in name:
            candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(f"No SCRFD ONNX candidates found in InsightFace cache: {cache_root}")

    candidates.sort(key=lambda path: (0 if path.name.lower() == "det_10g.onnx" else 1, len(path.parts), path.as_posix()))
    for candidate in candidates:
        if is_valid_onnx_model(candidate):
            return candidate

    raise FileNotFoundError(f"No valid SCRFD ONNX found in InsightFace cache: {cache_root}")


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
    dest = MODELS_DIR / "yolov8_person" / "1" / "model.plan"
    if artifact_ready(dest):
        print(f"Skipping YOLOv8n person detection; already present at {dest}")
        return

    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    model.export(
        format="engine",
        imgsz=640,
        half=True,
        batch=16,
        device=0,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path("yolov8n.engine").rename(dest)
    print(f"Saved to {dest}")


def prepare_savant_person_onnx():
    """Export the canonical person detector to a Savant-owned ONNX artifact."""
    print("Preparing Savant person detector ONNX...")
    dest = SAVANT_MODELS_DIR / "yolov8_person" / "1" / "yolov8n.onnx"
    if onnx_artifact_ready(dest):
        print(f"Skipping Savant person detector ONNX; already present at {dest}")
        return

    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    model.export(
        format="onnx",
        imgsz=640,
        batch=1,
        device="cpu",
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path("yolov8n.onnx").rename(dest)
    print(f"Saved to {dest}")

def export_yolov8_phone():
    """Export YOLOv8n phone detection to TensorRT."""
    print("Exporting YOLOv8n phone detection...")
    dest = MODELS_DIR / "yolov8_phone" / "1" / "model.plan"
    if artifact_ready(dest):
        print(f"Skipping YOLOv8n phone detection; already present at {dest}")
        return

    from ultralytics import YOLO

    phone_source = resolve_phone_model_source()

    model = YOLO(str(phone_source))
    onnx_path = PHONE_ONNX_SOURCE
    source_ready = is_valid_onnx_model(onnx_path)
    if not source_ready:
        model.export(
            format="onnx",
            imgsz=640,
            half=True,
            batch=1,
            device="cpu",
        )
        exported = phone_source.with_suffix(".onnx")
        if not exported.exists():
            raise FileNotFoundError(f"Expected exported ONNX artifact was not created: {exported}")
        shutil.copy2(exported, onnx_path)
    if not is_valid_onnx_model(onnx_path):
        raise ValueError(f"Phone detector ONNX source is not valid: {onnx_path}")

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
    dest = SAVANT_MODELS_DIR / "yolov8_phone" / "1" / "best.onnx"
    if onnx_artifact_ready(dest):
        print(f"Skipping Savant phone detector ONNX; already present at {dest}")
        return

    phone_source = resolve_phone_model_source()

    source = PHONE_ONNX_SOURCE
    source_ready = onnx_artifact_ready(source)
    if source_ready:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        print(f"Saved to {dest}")
        return

    try:
        from ultralytics import YOLO

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_pt = Path(temp_dir) / phone_source.name
            shutil.copy2(phone_source, temp_pt)
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
            shutil.copy2(exported, source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            print(f"Saved to {dest}")
            return
    except Exception:
        if not source_ready:
            raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    print(f"Saved to {dest}")


def prepare_savant_pose_onnx():
    """Prepare the Savant-owned ONNX source for the pose detector."""
    print("Preparing Savant pose detector ONNX...")
    dest = SAVANT_MODELS_DIR / "pose_detetcion" / "1" / "yolo26s-pose.onnx"
    if onnx_artifact_ready(dest):
        print(f"Skipping Savant pose detector ONNX; already present at {dest}")
        return

    pose_source = resolve_pose_model_source()

    source = POSE_ONNX_SOURCE
    source_ready = onnx_artifact_ready(source)
    if source_ready:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        print(f"Saved to {dest}")
        return

    try:
        from ultralytics import YOLO

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_pt = Path(temp_dir) / pose_source.name
            shutil.copy2(pose_source, temp_pt)
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
            shutil.copy2(exported, source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            print(f"Saved to {dest}")
            return
    except Exception:
        if not source_ready:
            raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
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

    dest = MODELS_DIR / "osnet" / "1" / "model.plan"
    if artifact_ready(dest):
        print(f"Skipping OSNet export; already present at {dest}")
        return

    dummy = torch.randn(1, 3, 256, 128)
    onnx_path = Path("/tmp/osnet_x1_0.onnx")
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        dynamo=False,
    )

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


def export_scrfd():
    """Download the official SCRFD 10G KPS ONNX model and prepare Savant/Triton artifacts."""
    print("Preparing SCRFD face detector ONNX...")

    savant_scrfd = SAVANT_MODELS_DIR / "scrfd" / "det_10g.onnx"
    triton_scrfd = MODELS_DIR / "scrfd"
    dest = triton_scrfd / "1" / "model.plan"

    if is_valid_onnx_model(savant_scrfd) and artifact_ready(dest):
        print(f"SCRFD already present at {savant_scrfd} and {dest}")
        return

    if savant_scrfd.exists() and not is_valid_onnx_model(savant_scrfd):
        print(f"Existing SCRFD artifact at {savant_scrfd} is not a valid ONNX model; refreshing it...")
        savant_scrfd.unlink()
    if dest.exists():
        print(f"Removing stale Triton SCRFD engine at {dest} before rebuild...")
        dest.unlink()

    triton_scrfd.parent.mkdir(parents=True, exist_ok=True)
    if triton_scrfd.exists():
        for stale_artifact in [triton_scrfd / "det_10g.onnx", triton_scrfd / "1" / "model.onnx"]:
            if stale_artifact.exists():
                stale_artifact.unlink()

    if not is_valid_onnx_model(savant_scrfd):
        from insightface.app import FaceAnalysis

        print("Downloading SCRFD det_10g.onnx via InsightFace buffalo_l cache...")
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))

        insightface_src = find_insightface_scrfd_onnx()
        print(f"Found SCRFD ONNX in InsightFace cache: {insightface_src}")
        savant_scrfd.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(insightface_src, savant_scrfd)

    if not is_valid_onnx_model(savant_scrfd):
        raise ValueError(f"SCRFD source artifact is not a valid ONNX model: {savant_scrfd}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import onnx

        onnx_model = onnx.load(savant_scrfd)
        input_name = onnx_model.graph.input[0].name
        subprocess.run([
            "trtexec",
            f"--onnx={savant_scrfd}",
            f"--saveEngine={dest}",
            "--fp16",
            f"--shapes={input_name}:1x3x640x640",
        ], check=True)
        print(f"Saved TensorRT engine to {dest}")
    except ImportError:
        raise RuntimeError("onnx is required to inspect the SCRFD source model before TensorRT export")
    except Exception as e:
        raise RuntimeError(f"Failed to convert SCRFD to TensorRT: {e}")

    print(f"Saved to {savant_scrfd}")


def export_arcface():
    """Export the canonical ArcFace ONNX artifact to TensorRT."""
    print("Exporting ArcFace...")
    arcface_source = ensure_arcface_source()

    dest = MODELS_DIR / "arcface" / "1" / "model.plan"
    if artifact_ready(dest):
        print(f"Skipping ArcFace export; already present at {dest}")
        return

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=[
            "yolov8_person",
            "yolov8_phone",
            "prepare_savant_pose_onnx",
            "arcface",
            "osnet",
            "scrfd",
            "prepare_savant_person_onnx",
            "prepare_savant_phone_onnx",
            "yolo26s_pose",
        ],
    )
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all:
        prepare_savant_person_onnx()
        prepare_savant_phone_onnx()
        prepare_savant_pose_onnx()
        export_arcface()
        export_osnet()
        export_scrfd()
    elif args.model:
        {
            "yolov8_person": export_yolov8_person,
            "yolov8_phone": export_yolov8_phone,
            "prepare_savant_pose_onnx": prepare_savant_pose_onnx,
            "yolo26s_pose": prepare_savant_pose_onnx,
            "arcface": export_arcface,
            "osnet": export_osnet,
            "scrfd": export_scrfd,
            "prepare_savant_person_onnx": prepare_savant_person_onnx,
            "prepare_savant_phone_onnx": prepare_savant_phone_onnx,
        }[args.model]()
    else:
        parser.print_help()
