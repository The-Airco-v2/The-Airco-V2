#!/usr/bin/env bash
# local.sh — start Airco Secure v2 for local development
#
# Usage:
#   ./local.sh            — infra + API only (no GPU services)
#   ./local.sh --gpu      — full local V2 backend (requires NVIDIA GPU)
#   ./local.sh --gpu-lite — local-lite GPU flow (person detector path only)
#   ./local.sh --down     — stop and remove all containers
#   ./local.sh --migrate  — run Alembic migrations only
#   ./local.sh --logs     — tail logs for all services
#
# Prerequisites:
#   - Docker + Docker Compose with NVIDIA Container Toolkit (for --gpu / --gpu-lite)
#   - Backend/.env.local exists (copy from .env.local.example and fill in Supabase values)
#   - Frontend: run separately with: cd ../Frontend && npm run dev

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/.env.local"
COMPOSE_FILE="$DIR/docker-compose.local.yml"
COMPOSE="docker compose"

if ! docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker-compose"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found."
  echo "Copy Backend/.env.local.example to Backend/.env.local and fill in your Supabase values."
  exit 1
fi

run_compose() {
  $COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

run_compose_gpu() {
  $COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile gpu "$@"
}

run_compose_gpu_full() {
  $COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile gpu-full "$@"
}

case "${1:-}" in
  --down)
    echo "Stopping Backend local stack..."
    run_compose_gpu_full down --remove-orphans
    run_compose_gpu down --remove-orphans
    ;;

  --migrate)
    echo "Running Alembic migrations..."
    run_compose run --rm api sh -c "cd /app/migrations && alembic upgrade head"
    ;;

  --logs)
    run_compose_gpu_full logs -f
    ;;

  --gpu)
    echo "Starting Airco Secure Backend (full GPU mode)..."

    echo ""
    echo "Ensuring local Triton identity models are built for this GPU stack..."
    bash "$DIR/scripts/ensure_local_triton_models.sh"

    echo ""
    echo "Ensuring local Savant detector models are prepared for this GPU stack..."
    bash "$DIR/scripts/ensure_local_savant_models.sh"

    run_compose_gpu_full up -d

    echo ""
    echo "Running Alembic migrations..."
    run_compose run --rm api sh -c "sleep 3 && cd /app/migrations && alembic upgrade head" || true

    echo ""
    echo "✓ Backend stack is up (GPU mode)."
    echo ""
    echo "  API:        http://localhost:8000"
    echo "  API docs:   http://localhost:8000/docs"
    echo "  MinIO:      http://localhost:9001  (user: airco / localdev)"
    echo "  Centrifugo: http://localhost:8088"
    echo "  go2rtc:     http://localhost:1984"
    echo "  Triton:     http://localhost:8002/v2/health/ready"
    echo ""
    echo "  Frontend: cd ../Frontend && npm run dev  →  http://localhost:3000"
    echo "  Stack:    gpu-full (includes identity-consumer)"
    echo "  To stop:  ./local.sh --down"
    echo "  Logs:     ./local.sh --logs"
    ;;

  --gpu-lite)
    echo "Starting Airco Secure Backend (local-lite GPU mode)..."

    echo ""
    echo "Ensuring local Savant detector models are prepared for this GPU stack..."
    bash "$DIR/scripts/ensure_local_savant_models.sh"

    run_compose_gpu up -d

    echo ""
    echo "Running Alembic migrations..."
    run_compose run --rm api sh -c "sleep 3 && cd /app/migrations && alembic upgrade head" || true

    echo ""
    echo "✓ Backend stack is up (GPU lite mode)."
    echo ""
    echo "  API:        http://localhost:8000"
    echo "  API docs:   http://localhost:8000/docs"
    echo "  MinIO:      http://localhost:9001  (user: airco / localdev)"
    echo "  Centrifugo: http://localhost:8088"
    echo "  go2rtc:     http://localhost:1984"
    echo ""
    echo "  Frontend: cd ../Frontend && npm run dev  →  http://localhost:3000"
    echo "  Stack:    gpu (no identity-consumer / no triton)"
    echo "  To stop:  ./local.sh --down"
    echo "  Logs:     ./local.sh --logs"
    ;;

  *)
    echo "Starting Airco Secure Backend (API-only mode, no GPU services)..."
    run_compose up -d

    echo ""
    echo "Running Alembic migrations..."
    run_compose run --rm api sh -c "sleep 3 && cd /app/migrations && alembic upgrade head" || true

    echo ""
    echo "✓ Backend stack is up."
    echo ""
    echo "  API:        http://localhost:8000"
    echo "  API docs:   http://localhost:8000/docs"
    echo "  MinIO:      http://localhost:9001  (user: airco / localdev)"
    echo "  Centrifugo: http://localhost:8088"
    echo "  go2rtc:     http://localhost:1984"
    echo ""
    echo "  Frontend: cd ../Frontend && npm run dev  →  http://localhost:3000"
    echo "  To stop:  ./local.sh --down"
    echo "  Logs:     ./local.sh --logs"
    ;;
esac
