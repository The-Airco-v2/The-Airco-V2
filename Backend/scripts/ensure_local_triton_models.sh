#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$DIR/services/triton/models"
STAMP_DIR="$MODELS_DIR/.local-build"
STAMP_FILE="$STAMP_DIR/gpu-full-ready"
BUILDER_VERSION="triton-24.08-identity-v5"
REQUIRED_MODELS=(
  "arcface"
  "osnet"
  "scrfd"
)

mkdir -p "$STAMP_DIR"

echo "Preparing local Triton identity models for gpu-full..."

ensure_model() {
  local model="$1"
  local artifact="$MODELS_DIR/$model/1/model.plan"
  local model_stamp="$STAMP_DIR/$model.ready"

  if [[ -s "$artifact" ]]; then
    echo "  - $model is current."
    printf '%s\n' "$BUILDER_VERSION" > "$model_stamp"
    return
  fi

  echo "  - exporting $model..."
  bash "$DIR/scripts/export_tensorrt_in_docker.sh" --model "$model"
  printf '%s\n' "$BUILDER_VERSION" > "$model_stamp"
}

SCRFD_ARTIFACT="$MODELS_DIR/scrfd/1/model.plan"
if [[ -f "$STAMP_FILE" ]] && grep -qx "$BUILDER_VERSION" "$STAMP_FILE" && [[ -s "$SCRFD_ARTIFACT" ]]; then
  ALL_CURRENT=true
  for model in "${REQUIRED_MODELS[@]}"; do
    if [[ ! -s "$MODELS_DIR/$model/1/model.plan" ]] || [[ ! -f "$STAMP_DIR/$model.ready" ]] || ! grep -qx "$BUILDER_VERSION" "$STAMP_DIR/$model.ready"; then
      ALL_CURRENT=false
      break
    fi
  done
  if [[ "$ALL_CURRENT" == true ]]; then
    echo "Local Triton model cache is current."
    exit 0
  fi
fi

for model in "${REQUIRED_MODELS[@]}"; do
  ensure_model "$model"
done

printf '%s\n' "$BUILDER_VERSION" > "$STAMP_FILE"
echo "Local Triton models are ready."

# Also ensure SCRFD ONNX model for savant-pipeline (produced by the scrfd export)
SCRFD_DEST="$DIR/services/savant-pipeline/models/scrfd/det_10g.onnx"
if [[ -s "$SCRFD_DEST" ]]; then
  echo "SCRFD ONNX model is present."
else
  echo "SCRFD ONNX model is missing after export; rebuilding via shared exporter..."
  mkdir -p "$(dirname "$SCRFD_DEST")"
  bash "$DIR/scripts/export_tensorrt_in_docker.sh" --model scrfd || true
  if [[ ! -s "$SCRFD_DEST" ]]; then
    echo "WARNING: SCRFD model not available. Face detection will use fallback heuristic."
  fi
fi
