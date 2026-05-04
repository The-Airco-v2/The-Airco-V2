#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$DIR/services/triton/models"
STAMP_DIR="$MODELS_DIR/.local-build"
STAMP_FILE="$STAMP_DIR/gpu-full-ready"
BUILDER_VERSION="triton-24.08-identity-v3"
REQUIRED_MODELS=(
  "arcface"
  "osnet"
)

mkdir -p "$STAMP_DIR"

if [[ -f "$STAMP_FILE" ]] && grep -qx "$BUILDER_VERSION" "$STAMP_FILE"; then
  echo "Local Triton model cache is current."
  exit 0
fi

echo "Preparing local Triton identity models for gpu-full..."

ensure_model() {
  local model="$1"
  local artifact="$MODELS_DIR/$model/1/model.plan"
  local model_stamp="$STAMP_DIR/$model.ready"

  if [[ -s "$artifact" && -f "$model_stamp" ]] && grep -qx "$BUILDER_VERSION" "$model_stamp"; then
    echo "  - $model is current."
    return
  fi

  echo "  - exporting $model..."
  bash "$DIR/scripts/export_tensorrt_in_docker.sh" --model "$model"
  printf '%s\n' "$BUILDER_VERSION" > "$model_stamp"
}

for model in "${REQUIRED_MODELS[@]}"; do
  ensure_model "$model"
done

printf '%s\n' "$BUILDER_VERSION" > "$STAMP_FILE"
echo "Local Triton models are ready."

# Also ensure SCRFD ONNX model for savant-pipeline (runs via onnxruntime, not Triton)
SCRFD_DEST="$DIR/services/savant-pipeline/models/scrfd/det_10g.onnx"
if [[ -s "$SCRFD_DEST" ]]; then
  echo "SCRFD ONNX model is present."
else
  echo "Downloading SCRFD ONNX model via insightface buffalo_l..."
  mkdir -p "$(dirname "$SCRFD_DEST")"
  docker run --rm \
    -v "$DIR:/workspace/v2" \
    airco-v2-model-export:latest \
    python3 /workspace/v2/scripts/export_tensorrt.py --model scrfd || true
  if [[ ! -s "$SCRFD_DEST" ]]; then
    echo "WARNING: SCRFD model not available. Face detection will use fallback heuristic."
  fi
fi
