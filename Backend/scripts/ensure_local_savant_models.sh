#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$DIR/services/savant-pipeline/models"
STAMP_DIR="$MODELS_DIR/.local-build"
STAMP_FILE="$STAMP_DIR/gpu-detectors-ready"
BUILDER_VERSION="savant-detectors-v2"
REQUIRED_MODELS=(
  "prepare_savant_person_onnx"
  "prepare_savant_phone_onnx"
)

mkdir -p "$STAMP_DIR"

if [[ -f "$STAMP_FILE" ]] && grep -qx "$BUILDER_VERSION" "$STAMP_FILE"; then
  echo "Local Savant detector model cache is current."
  exit 0
fi

echo "Preparing local Savant detector models..."

ensure_model() {
  local model="$1"
  local model_stamp="$STAMP_DIR/$model.ready"

  case "$model" in
    prepare_savant_person_onnx)
      local artifact="$MODELS_DIR/yolov8_person/1/yolov8n.onnx"
      ;;
    prepare_savant_phone_onnx)
      local artifact="$MODELS_DIR/yolov8_phone/1/best.onnx"
      ;;
    *)
      echo "Unknown Savant model target: $model" >&2
      exit 1
      ;;
  esac

  if [[ -s "$artifact" && -f "$model_stamp" ]] && grep -qx "$BUILDER_VERSION" "$model_stamp"; then
    echo "  - $model is current."
    return
  fi

  echo "  - preparing $model..."
  bash "$DIR/scripts/export_tensorrt_in_docker.sh" --model "$model"
  printf '%s\n' "$BUILDER_VERSION" > "$model_stamp"
}

for model in "${REQUIRED_MODELS[@]}"; do
  ensure_model "$model"
done

printf '%s\n' "$BUILDER_VERSION" > "$STAMP_FILE"
echo "Local Savant detector models are ready."
