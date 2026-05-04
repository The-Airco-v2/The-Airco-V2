#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${1:?env file required}"
COMPOSE_FILE="docker-compose.yml"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-2}"

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

wait_for_command() {
  local label="$1"
  shift
  local deadline=$((SECONDS + TIMEOUT_SECONDS))

  until "$@" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for ${label} after ${TIMEOUT_SECONDS}s" >&2
      "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps || true
      exit 1
    fi
    sleep "${INTERVAL_SECONDS}"
  done
}

wait_for_command "timescaledb" \
  "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T timescaledb pg_isready -U airco

wait_for_command "redis" \
  "${COMPOSE_CMD[@]}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T redis redis-cli ping
