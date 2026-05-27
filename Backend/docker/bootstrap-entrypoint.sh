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

# ── SSH Server ───────────────────────────────────────────────────────────────
echo "Starting SSH server..."
mkdir -p /var/run/sshd
# Permit root login and configure password authentication
echo "root:root" | chpasswd
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config || true
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config || true
sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config || true
sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config || true
if [ -n "${SSH_PUBLIC_KEY:-}" ]; then
    echo "Adding authorized SSH public key..."
    mkdir -p /root/.ssh
    echo "${SSH_PUBLIC_KEY}" >> /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/authorized_keys
fi
ssh-keygen -A
/usr/sbin/sshd

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
    --socks5-server=127.0.0.1:1055 \
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

# Start SOCKS5 port forwarders to VPS over Tailscale SOCKS5 proxy
echo "Starting SOCKS5 port forwarders to VPS..."
socat TCP4-LISTEN:5432,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:5432,socksport=1055 &
socat TCP4-LISTEN:6379,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:6379,socksport=1055 &
socat TCP4-LISTEN:9000,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:9000,socksport=1055 &
socat TCP4-LISTEN:8088,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:8088,socksport=1055 &
socat TCP4-LISTEN:8554,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:8554,socksport=1055 &
socat TCP4-LISTEN:1984,fork,reuseaddr SOCKS4A:127.0.0.1:${AIRCO_HUB_HOST}:1984,socksport=1055 &

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

# Override AIRCO_HUB_HOST to 127.0.0.1 so services connect via local socat tunnels
sed -i 's/^AIRCO_HUB_HOST=.*/AIRCO_HUB_HOST=127.0.0.1/' .env.production
grep -q '^AIRCO_HUB_HOST=' .env.production || echo "AIRCO_HUB_HOST=127.0.0.1" >> .env.production

grep -q IMAGE_REGISTRY .env.production || echo "IMAGE_REGISTRY=ghcr.io/the-airco-v2" >> .env.production
grep -q IMAGE_TAG .env.production       || echo "IMAGE_TAG=latest"                      >> .env.production

echo "--- .env.production ---"
cat .env.production
echo "-----------------------"

# ── 5. Start GPU stack via Docker ────────────────────────────────────────────
echo "[5/5] Starting GPU stack via Docker..."

# Start dockerd in the background
dockerd &
echo "Waiting for dockerd to start..."
for i in {1..10}; do
    if docker info >/dev/null 2>&1; then
        echo "Docker daemon is ready!"
        break
    fi
    sleep 2
done

# Diagnostic info — useful if Docker still fails
echo "--- Docker diagnostics ---"
echo "Docker version: $(docker --version 2>&1 || echo 'N/A')"
echo "daemon.json:"
cat /etc/docker/daemon.json 2>/dev/null || echo "(missing)"
echo "--------------------------"

# Smoke-test: if 'docker info' fails, the rest won't work either
if ! docker info >/dev/null 2>&1; then
    echo "WARNING: 'docker info' failed — dumping full output:"
    docker info 2>&1 || true
fi

echo "${GHCR_PAT}" | docker login ghcr.io -u nick2580 --password-stdin

docker compose -f docker-compose.gpu.yml --env-file .env.production pull
docker compose -f docker-compose.gpu.yml --env-file .env.production up -d

echo ""
echo "================================================================"
echo "  Bootstrap complete — $(date -u)"
echo "  Containers:"
docker ps
echo "================================================================"

# Keep alive
sleep infinity
