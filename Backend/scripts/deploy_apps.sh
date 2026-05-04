#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MODE="${1:-incremental}"
ENV_FILE="${2:?env file required}"
COMPOSE_FILE="docker-compose.yml"
SERVICES=(
  api
  identity-consumer
  analytics-consumer
  snapshot-consumer
  ws-publisher
  frontend
  savant-pipeline
)

if [[ "${ENV_FILE}" != /* ]]; then
  ENV_FILE="${PROJECT_DIR}/${ENV_FILE}"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif docker-compose --version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

cd "${PROJECT_DIR}"

if [[ "${MODE}" == "full-rebuild" ]]; then
  "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build --no-cache "${SERVICES[@]}"
  "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --force-recreate "${SERVICES[@]}"
elif [[ "${MODE}" == "incremental" ]]; then
  "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build "${SERVICES[@]}"
else
  echo "unsupported deploy mode: ${MODE}" >&2
  exit 1
fi
