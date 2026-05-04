#!/usr/bin/env bash
set -euo pipefail

# Run TensorRT model export inside a dedicated builder image so the host
# machine does not need the full Python/TensorRT export stack installed.
#
# Usage:
#   ./scripts/export_tensorrt_in_docker.sh --model yolov8_phone
#   ./scripts/export_tensorrt_in_docker.sh --model arcface
#   ./scripts/export_tensorrt_in_docker.sh --all

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
IMAGE_NAME="airco-v2-model-export:latest"

docker build \
  -f "$DIR/docker/export-models.Dockerfile" \
  -t "$IMAGE_NAME" \
  "$ROOT"

docker run --rm --gpus all \
  -v "$ROOT:/workspace" \
  -w /workspace \
  "$IMAGE_NAME" \
  python v2/scripts/export_tensorrt.py "$@"

