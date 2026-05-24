---
name: hostinger-management
description: Manage the Hostinger KVM 2 VPS that runs the Airco CPU stack — SSH access, Docker compose operations, service restarts, log inspection, env file updates, and deployment procedures.
---

# Hostinger VPS Management Skill

Use this skill to manage the always-on Hostinger VPS that runs the CPU half of the Airco stack.

## Connection Details

| Property | Value |
|---|---|
| **Public IP** | `72.61.239.69` |
| **Tailscale IP** | `100.103.80.105` |
| **Tailscale hostname** | `airco-hub` |
| **SSH key** | `~/.ssh/id_ed25519` |
| **SSH command** | `ssh -i ~/.ssh/id_ed25519 root@72.61.239.69` |
| **App directory** | `/app/airco/` |
| **Env file** | `/app/airco/.env.production` |

## Quick SSH

```bash
# Direct SSH
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69

# Run a remote command
ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_ed25519 root@72.61.239.69 '<command>'
```

## Compose Stack Operations

All compose files live at `/app/airco/`. The stack is split into:
- `docker-compose.cpu.yml` — application services (API, Redis, Postgres, MinIO, Centrifugo, go2rtc, mediamtx, consumers)
- `docker-compose.proxy.yml` — Caddy reverse proxy / TLS terminator

> [!IMPORTANT]
> `/app/airco/` is a deployed app directory, not a git checkout. The GitHub Actions
> deploy job stages files there with SCP and then runs `docker compose`. Do not
> assume `git pull` is part of the Hostinger deploy flow.

```bash
# Check status of all services
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml -f docker-compose.proxy.yml ps'

# Restart a single service
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'

# Restart all CPU services
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart'

# Pull latest images and restart (rolling deploy)
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml pull && docker compose -f docker-compose.cpu.yml up -d'

# Tail logs for a service
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml logs -f --tail=100 api'

# Run Alembic migrations
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml exec api alembic upgrade head'

# Health check
ssh root@72.61.239.69 'curl -s http://localhost:8000/health'
```

## Service Port Reference

| Service | Internal Port | Notes |
|---|---|---|
| FastAPI (api) | 8000 | Public via Caddy → api.the-airco.net |
| TimescaleDB | 5432 | Internal only (bound to 127.0.0.1) |
| Redis | 6379 | Internal only (bound to 127.0.0.1) |
| MinIO | 9000 | Public via Caddy → api.the-airco.net/minio |
| Centrifugo | 8088 | Public via Caddy WebSocket |
| go2rtc | 1984 (HTTP), 8554 (RTSP) | Public via media.the-airco.net |
| Caddy | 80, 443 | TLS terminator |

## Env File Management

```bash
# View current env file
ssh root@72.61.239.69 'cat /app/airco/.env.production'

# Update a specific variable
ssh root@72.61.239.69 'sed -i "s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID=<NEW_ID>/" /app/airco/.env.production'

# Verify the change
ssh root@72.61.239.69 'grep RUNPOD_POD_ID /app/airco/.env.production'
```

## Volume-Mount Hotfix: gpu_controller.py

The API container bind-mounts `gpu_controller.py` from the host:
```
/app/airco/gpu_controller.py → /app/api/gpu_controller.py (in container)
```

To update the GPU controller without rebuilding the image:
```bash
# Copy the updated file to the VPS
scp -i ~/.ssh/id_ed25519 Backend/services/api/api/gpu_controller.py root@72.61.239.69:/app/airco/gpu_controller.py

# Restart the API to pick up changes
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'
```

> [!NOTE]
> The bind-mount path is defined in `docker-compose.cpu.yml` under the `api` service `volumes:` section.

## Tailscale on VPS

The VPS runs `tailscaled` as a system service and is registered as `airco-hub` on the tailnet.

```bash
# Check Tailscale status (shows connected peers — GPU pod should appear as airco-gpu)
ssh root@72.61.239.69 'tailscale status'

# Ping the GPU pod via Tailscale
ssh root@72.61.239.69 'tailscale ping airco-gpu'

# Get detailed Tailscale info
ssh root@72.61.239.69 'tailscale status --json | python3 -m json.tool'
```

## Common Debugging Procedures

### API not responding
```bash
# Check if container is running
ssh root@72.61.239.69 'docker ps | grep api'
# Check recent logs
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml logs --tail=50 api'
# Check health
ssh root@72.61.239.69 'curl -sv http://localhost:8000/health'
```

### Database / Migration issues
```bash
# Check Postgres is running
ssh root@72.61.239.69 'docker ps | grep timescaledb'
# Run pending migrations
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml exec api alembic upgrade head'
# Check migration state
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml exec api alembic current'
```

### Caddy / TLS issues
```bash
# Check Caddy logs
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.proxy.yml logs caddy'
# Verify Caddyfile
ssh root@72.61.239.69 'cat /app/airco/deploy/Caddyfile'
# Force certificate renewal
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.proxy.yml exec caddy caddy reload --config /etc/caddy/Caddyfile'
```

### GPU pod not connecting
```bash
# Check Tailscale for airco-gpu peer
ssh root@72.61.239.69 'tailscale status'
# If not present, check RunPod API for pod status (see runpod-lifecycle skill)
# Then check API logs for GPU controller errors
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml logs --tail=50 api | grep -i gpu'
```

## Full Redeploy Procedure

Preferred path is GitHub Actions:

1. Run `.github/workflows/build-images.yml`
2. Leave `full_rebuild=false` for a normal redeploy
3. Set `full_rebuild=true` only if you need to force rebuilding all images

That workflow:
- rebuilds only the image jobs whose path filters matched unless `full_rebuild=true`
- uploads `docker-compose.cpu.yml`, `gpu_controller.py`, `runpod_client.py`, and `routes/sessions.py` to `/app/airco/.gha-deploy/`
- stages those files into `/app/airco/`
- runs `docker compose pull`, `up -d`, and Alembic on the VPS

Manual SSH fallback:

```bash
# 1. SSH into VPS
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69

# 2. Login to GHCR (use PAT <GHCR_PAT>)
docker login ghcr.io -u nick2580

# 3. Pull new images
cd /app/airco && docker compose -f docker-compose.cpu.yml pull

# 4. Restart services (zero-downtime order: infra first, then api)
docker compose -f docker-compose.cpu.yml up -d

# 5. Run migrations if schema changed
docker compose -f docker-compose.cpu.yml exec api alembic upgrade head
```
