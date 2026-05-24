#!/usr/bin/env bash
# Provisioning script for Hostinger VPS
set -euo pipefail

echo "=========================================="
echo "Starting Hostinger VPS Provisioning"
echo "=========================================="

# 1. Update Apt
echo "--> Updating package repositories..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y

# Install prerequisite tools
apt-get install -y curl git ufw iptables gnupg software-properties-common

# 2. Install Docker
if ! command -v docker &> /dev/null; then
    echo "--> Installing Docker Engine..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
else
    echo "--> Docker is already installed."
fi

echo "--> Verifying Docker Compose..."
docker compose version

# 3. Install Tailscale
if ! command -v tailscale &> /dev/null; then
    echo "--> Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "--> Tailscale is already installed."
fi

# 4. Authenticate Tailscale
echo "--> Authenticating Tailscale..."
# Ensure tailscaled service is running
systemctl enable --now tailscaled

tailscale up \
    --authkey="<TAILSCALE_AUTHKEY>" \
    --advertise-tags=tag:airco \
    --accept-routes=false

echo "--> Tailscale IP:"
tailscale ip -4

# 5. Configure Firewall (ufw)
echo "--> Configuring Firewall (ufw)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp

# Try to allow tailscale0 interface, if it exists
if ip link show tailscale0 &> /dev/null; then
    ufw allow in on tailscale0
else
    echo "Warning: tailscale0 interface not found yet. UFW rule for tailscale0 not added. Adding tailscale subnet bypass."
    # Allow traffic from Tailscale interface by subnet if interface not active
    ufw allow in from 100.64.0.0/10
fi

ufw --force enable
ufw status verbose

echo "=========================================="
echo "Hostinger VPS Provisioning Complete!"
echo "=========================================="
