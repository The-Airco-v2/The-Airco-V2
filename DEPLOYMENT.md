# Deployment Runbook

Current production deployment for Airco Secure V2. Architecture context lives in `CLAUDE.md`; this file is the operational flow.

## Production topology

| Layer | Provider | Purpose | Current endpoint |
|---|---|---|---|
| Frontend | Cloudflare Pages | React SPA | `https://the-airco-v2.pages.dev` / `https://app.the-airco.net` |
| CPU stack | Hostinger KVM 2 | API, Postgres, Redis, MinIO, Centrifugo, go2rtc, mediamtx, CPU consumers, Caddy | `72.61.239.69` / `100.103.80.105` |
| GPU stack | RunPod pod | Triton, Savant, identity pipeline, alternate Ultimate adapter | RunPod pod `an5cwp48emcmjj` |
| Registry | GHCR | Service images | `ghcr.io/the-airco-v2/airco-*` |
| Overlay network | Tailscale | CPU↔GPU private traffic | `airco-hub` / `airco-gpu` |

## End-to-end deployment flow

### 1. Source of truth

- `main` is the production branch.
- CPU host-managed files originate from the repo:
  - `Backend/docker-compose.cpu.yml`
  - `Backend/services/api/api/gpu_controller.py`
  - `Backend/services/api/api/runpod_client.py`
  - `Backend/services/api/api/routes/sessions.py`
- Runtime env on the VPS lives in `/app/airco/.env.production`.
- GPU pod bootstrap repo lives at `/workspace/The-Airco-V2`.

### 2. Build and publish images

Workflow: `.github/workflows/build-images.yml`

Triggers:
- `push` to `main`
- `workflow_dispatch`

Behavior:
- Path filters decide which image jobs run on normal pushes.
- Manual dispatch has `full_rebuild`:
  - `false` → only changed image jobs rebuild
  - `true` → all image jobs rebuild
- Images are tagged `:sha-<short>` and, on `main`, also `:latest`.

Important path-filter rules:
- `Backend/shared/**` rebuilds all Python service images.
- `Backend/services/savant-pipeline/**` rebuilds only `airco-savant-pipeline`.
- `Backend/services/triton/**` rebuilds only `airco-triton`.
- `Backend/services/ultimate-adapter/**` or `Ultimate-Tracker/**` rebuild `airco-ultimate-adapter`.

### 3. Deploy CPU stack to Hostinger

The CPU stack does **not** run from a git checkout. `/app/airco/` is a deployed app directory with compose files, env file, and bind-mounted hotfix files.

The deploy job in `.github/workflows/build-images.yml` does this:

1. Copies the CPU deploy-managed files to `/app/airco/.gha-deploy/` using `appleboy/scp-action`
2. Stages them into `/app/airco/`
3. Runs:
   - `docker compose -f docker-compose.cpu.yml --env-file .env.production pull`
   - `docker compose -f docker-compose.cpu.yml --env-file .env.production up -d`
   - `docker compose -f docker-compose.cpu.yml --env-file .env.production exec -T api alembic upgrade head`

Manual redeploy via GitHub Actions:
- Run `Build & push images`
- Leave `full_rebuild=false` for a lightweight redeploy
- Set `full_rebuild=true` only when you really want all images rebuilt

Manual redeploy via SSH:

```bash
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69
cd /app/airco
docker compose -f docker-compose.cpu.yml --env-file .env.production pull
docker compose -f docker-compose.cpu.yml --env-file .env.production up -d
docker compose -f docker-compose.cpu.yml --env-file .env.production exec -T api alembic upgrade head
```

### 4. GPU pod bootstrap and runtime

The GPU pod is resumed and stopped by the API through RunPod GraphQL. On pod startup:

1. `dockerArgs` launches `bash -c "..."` and writes logs to `/workspace/bootstrap.log`
2. Installs `curl` and `git`
3. Starts Tailscale and joins as `airco-gpu`
4. Clones or pulls `/workspace/The-Airco-V2`
5. Runs `Backend/scripts/gpu_bootstrap.sh`
6. `gpu_bootstrap.sh` installs Podman + `podman-compose`, logs into GHCR, pulls GPU images, and runs `podman-compose -f docker-compose.gpu.yml up -d`

The GPU pod uses Podman instead of Docker because RunPod community pods do not provide the privileges Docker needs for bridge networking and iptables.

### 5. Frontend deployment

Cloudflare Pages builds from `Frontend/`:

- Build command: `npm run build`
- Output dir: `dist`
- Required env:
  - `VITE_API_URL=https://api.the-airco.net`
  - `VITE_WS_URL=wss://api.the-airco.net/centrifugo/connection/websocket`
  - `VITE_GO2RTC_URL=https://media.the-airco.net`

### 6. Session-driven GPU lifecycle

- API receives session start
- `gpu_controller.py` resumes the RunPod pod
- API waits on `GPU_HEALTH_TARGET`
- GPU services connect back to Hostinger over Tailscale and process the session
- Idle timeout stops the pod when no active sessions remain

## Hostinger CPU stack

SSH: `ssh -i ~/.ssh/id_ed25519 root@72.61.239.69`

Key paths:
- App dir: `/app/airco/`
- Env file: `/app/airco/.env.production`
- Deploy staging dir: `/app/airco/.gha-deploy/`

Key compose files:
- `docker-compose.cpu.yml`
- `docker-compose.proxy.yml`

Useful commands:

```bash
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml -f docker-compose.proxy.yml ps'
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml logs -f api'
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'
ssh root@72.61.239.69 'tailscale status'
```

## RunPod GPU stack

Key facts:
- Pod ID: `an5cwp48emcmjj`
- Persistent volume: `/workspace`
- Repo checkout: `/workspace/The-Airco-V2`
- Bootstrap log: `/workspace/bootstrap.log`

Useful checks:

```bash
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale status'
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 \
  "ssh -o StrictHostKeyChecking=no root@<airco-gpu-tailscale-ip> 'cat /workspace/bootstrap.log'"
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 \
  "ssh -o StrictHostKeyChecking=no root@<airco-gpu-tailscale-ip> 'podman ps && nvidia-smi'"
```

## Tailscale

- Hostinger node: `airco-hub` → `100.103.80.105`
- GPU node: `airco-gpu` → dynamic Tailscale IP
- `AIRCO_HUB_HOST=100.103.80.105` is how GPU services locate the CPU host

If the GPU pod fails to appear in `tailscale status`:
- check RunPod uptime
- inspect `/workspace/bootstrap.log`
- inspect `/tmp/tailscaled.log`
- rotate expired auth key if needed

## Cross-domain auth

Production cookie settings:
- `SESSION_COOKIE_DOMAIN=.the-airco.net`
- `SESSION_SECURE_COOKIE=true`
- `SESSION_SAME_SITE=none`

This is required because the SPA runs on `app.the-airco.net` and the API runs on `api.the-airco.net`.

## Operational gotchas

- `/app/airco/` on Hostinger is not a git repo. Do not run `git pull` there.
- Lightweight manual deploys should use `workflow_dispatch` with `full_rebuild=false`.
- Heavy GPU images are expected to take much longer than CPU images when they do rebuild.
- Recreating the RunPod pod can change its Tailscale IP, but the hostname remains `airco-gpu`.
