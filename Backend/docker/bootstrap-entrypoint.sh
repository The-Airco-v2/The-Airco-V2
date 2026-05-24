#!/bin/bash
# bootstrap-entrypoint.sh — RunPod GPU pod entrypoint
# Pre-installed in the airco-runpod-bootstrap image.
# All secrets come from RunPod pod environment variables.
#
# Required env vars:
#   TAILSCALE_AUTHKEY  — Tailscale auth key (generate at login.tailscale.com/admin/settings/keys)
#   GHCR_PAT           — GitHub PAT with read:packages scope
#   AIRCO_HUB_HOST     — Tailscale IP of the Hostinger VPS (100.103.80.105)
#   POSTGRES_PASSWORD, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
#   CENTRIFUGO_API_KEY, CENTRIFUGO_TOKEN_SECRET, SESSION_SECRET,
#   SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
#   PUBLIC_MINIO_URL

set -euo pipefail
exec &> >(tee -a /workspace/bootstrap.log)

TAILSCALE_SOCKET=/tmp/tailscaled.sock

echo "================================================================"
echo "  Airco RunPod Bootstrap — $(date -u)"
echo "================================================================"

# ── 1. Tailscale ──────────────────────────────────────────────────────────────
echo "[1/5] Starting Tailscale..."
mkdir -p /var/run/tailscale
tailscaled \
    --state=/workspace/tailscale.state \
    --socket="${TAILSCALE_SOCKET}" \
    --tun=userspace-networking \
    >/tmp/tailscaled.log 2>&1 &
TAILSCALED_PID=$!
sleep 8

if ! kill -0 "${TAILSCALED_PID}" 2>/dev/null; then
    echo "ERROR: tailscaled exited during bootstrap."
    echo "--- /tmp/tailscaled.log ---"
    cat /tmp/tailscaled.log || true
    echo "---------------------------"
    exit 1
fi

if [ -z "${TAILSCALE_AUTHKEY:-}" ]; then
    echo "ERROR: TAILSCALE_AUTHKEY not set. Cannot connect to tailnet."
    exit 1
fi

tailscale --socket="${TAILSCALE_SOCKET}" up \
    --authkey="${TAILSCALE_AUTHKEY}" \
    --accept-routes=false \
    --hostname=airco-gpu

echo "TAILSCALE_OK — $(tailscale --socket="${TAILSCALE_SOCKET}" ip)"

# ── 2. NVIDIA CDI spec ────────────────────────────────────────────────────────
echo "[2/5] Generating NVIDIA CDI spec..."
mkdir -p /etc/cdi
nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml 2>/dev/null || \
    echo "WARNING: CDI generation failed (GPU not available yet?)"
nvidia-ctk cdi list 2>/dev/null || true

# ── 3. Clone / update repository ─────────────────────────────────────────────
echo "[3/5] Syncing repository..."
REPO=/workspace/The-Airco-V2

if [ -z "${GHCR_PAT:-}" ]; then
    echo "ERROR: GHCR_PAT not set."
    exit 1
fi

if [ -d "${REPO}/.git" ]; then
    echo "Repo exists, refreshing shallow checkout..."
    git -C "${REPO}" fetch --depth 1 origin main
    git -C "${REPO}" reset --hard origin/main
else
    echo "Cloning shallow checkout..."
    git clone --depth 1 --single-branch --branch main \
        "https://nick2580:${GHCR_PAT}@github.com/The-Airco-v2/The-Airco-V2.git" "${REPO}"
fi

# ── 4. Write .env.production ──────────────────────────────────────────────────
echo "[4/5] Writing .env.production..."
cd "${REPO}/Backend"

env | grep -E '^(POSTGRES|MINIO|CENTRIFUGO|SUPABASE|SESSION|AIRCO|PUBLIC|IMAGE|TENANT|GHCR)' \
    > .env.production

grep -q IMAGE_REGISTRY .env.production || echo "IMAGE_REGISTRY=ghcr.io/the-airco-v2" >> .env.production
grep -q IMAGE_TAG .env.production       || echo "IMAGE_TAG=latest"                      >> .env.production

echo "--- .env.production ---"
cat .env.production
echo "-----------------------"

# ── 5. Start GPU stack via Podman ────────────────────────────────────────────
echo "[5/5] Starting GPU stack via Podman..."
echo "${GHCR_PAT}" | podman login ghcr.io -u nick2580 --password-stdin

podman-compose -f docker-compose.gpu.yml --env-file .env.production pull
podman-compose -f docker-compose.gpu.yml --env-file .env.production up -d

echo ""
echo "================================================================"
echo "  Bootstrap complete — $(date -u)"
echo "  Containers:"
podman ps
echo "================================================================"

# Keep alive
sleep infinity
