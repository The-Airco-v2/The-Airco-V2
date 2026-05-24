#!/bin/bash
# gpu_bootstrap.sh — Full GPU stack bootstrap for RunPod community pods.
#
# Called by the RunPod pod entrypoint AFTER Tailscale is already connected.
# Uses Podman (rootless-capable, no iptables needed) instead of Docker daemon.
#
# Required environment variables (set on pod via RunPod env):
#   POSTGRES_PASSWORD, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
#   CENTRIFUGO_API_KEY, CENTRIFUGO_TOKEN_SECRET, SESSION_SECRET,
#   SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
#   AIRCO_HUB_HOST, PUBLIC_MINIO_URL
#   GHCR_PAT  — GitHub PAT with read:packages scope (for pulling images)
#
# The script writes all output to /workspace/bootstrap.log (in addition to stdout).

set -euxo pipefail
exec &> >(tee -a /workspace/bootstrap.log)

echo "================================================================"
echo "  Airco GPU Bootstrap — $(date -u)"
echo "================================================================"

# ── 1. System packages ────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  podman \
  iptables \
  python3-pip \
  ca-certificates \
  curl \
  git \
  gnupg \
  lsb-release

# podman-compose for docker-compose.gpu.yml
pip3 install --quiet podman-compose

echo "Podman version: $(podman --version)"

# ── 2. NVIDIA Container Toolkit ──────────────────────────────────────────────
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL \
  "https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list" \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update -qq
apt-get install -y nvidia-container-toolkit

# Generate CDI spec so Podman can access the GPU without a daemon
mkdir -p /etc/cdi
nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
echo "CDI spec written — GPU devices:"
nvidia-ctk cdi list 2>/dev/null || true

# Configure NVIDIA runtime for crun (Podman's default OCI runtime)
nvidia-ctk runtime configure --runtime=crun

# ── 3. Repository ─────────────────────────────────────────────────────────────
REPO_DIR=/workspace/The-Airco-V2
if [ -d "$REPO_DIR/.git" ]; then
  echo "Repo exists, pulling latest..."
  git -C "$REPO_DIR" pull
else
  echo "Cloning repository..."
  git clone \
    "https://nick2580:${GHCR_PAT}@github.com/The-Airco-v2/The-Airco-V2.git" \
    "$REPO_DIR"
fi

# ── 4. .env.production ────────────────────────────────────────────────────────
BACKEND_DIR="$REPO_DIR/Backend"
cd "$BACKEND_DIR"

# Write env file from pod environment variables
env | grep -E '^(POSTGRES|MINIO|CENTRIFUGO|SUPABASE|SESSION|AIRCO|PUBLIC|IMAGE|TENANT)' \
  > .env.production

# Ensure IMAGE_REGISTRY is set (images live in ghcr.io/the-airco-v2)
grep -q IMAGE_REGISTRY .env.production || echo "IMAGE_REGISTRY=ghcr.io/the-airco-v2" >> .env.production
grep -q IMAGE_TAG .env.production       || echo "IMAGE_TAG=latest"                      >> .env.production

echo "--- .env.production ---"
cat .env.production
echo "--- end ---"

# ── 5. GHCR login & image pull ───────────────────────────────────────────────
echo "Logging in to GHCR..."
echo "${GHCR_PAT}" | podman login ghcr.io -u nick2580 --password-stdin

echo "Pulling GPU compose images..."
podman-compose -f docker-compose.gpu.yml --env-file .env.production pull

# ── 6. Start the GPU stack ────────────────────────────────────────────────────
echo "Starting GPU compose stack..."
podman-compose -f docker-compose.gpu.yml --env-file .env.production up -d

echo ""
echo "================================================================"
echo "  Bootstrap complete — $(date -u)"
echo "  Containers:"
podman ps
echo "================================================================"

# Keep container alive so the pod doesn't exit
sleep infinity
