#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$DIR/services/savant-pipeline/models"
STAMP_DIR="$MODELS_DIR/.local-build"
STAMP_FILE="$STAMP_DIR/gpu-detectors-ready"
BUILDER_VERSION="savant-detectors-v3"
REQUIRED_MODELS=(
  "prepare_savant_person_onnx"
  "prepare_savant_phone_onnx"
  "prepare_savant_pose_onnx"
)

mkdir -p "$STAMP_DIR"

artifact_ready() {
  local path="$1"
  [[ -s "$path" ]] || return 1

  local size
  size=$(wc -c < "$path")
  [[ "$size" -ge 1048576 ]] || return 1

  if head -c 128 "$path" | grep -q "git-lfs.github.com/spec/v1"; then
    return 1
  fi

  return 0
}

if [[ -f "$STAMP_FILE" ]] && grep -qx "$BUILDER_VERSION" "$STAMP_FILE"; then
  all_current=true
  for model in "${REQUIRED_MODELS[@]}"; do
    case "$model" in
      prepare_savant_person_onnx)
        artifact="$MODELS_DIR/yolov8_person/1/yolov8n.onnx"
        ;;
      prepare_savant_phone_onnx)
        artifact="$MODELS_DIR/yolov8_phone/1/best.onnx"
        ;;
      prepare_savant_pose_onnx)
        artifact="$MODELS_DIR/pose_detetcion/1/yolo26s-pose.onnx"
        ;;
    esac

    if ! artifact_ready "$artifact"; then
      all_current=false
      break
    fi
  done

  if [[ "$all_current" == true ]]; then
    echo "Local Savant detector model cache is current."
    exit 0
  fi
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
    prepare_savant_pose_onnx)
      local artifact="$MODELS_DIR/pose_detetcion/1/yolo26s-pose.onnx"
      ;;
    *)
      echo "Unknown Savant model target: $model" >&2
      exit 1
      ;;
  esac

  if artifact_ready "$artifact"; then
    echo "  - $model is current."
    printf '%s\n' "$BUILDER_VERSION" > "$model_stamp"
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
