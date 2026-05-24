# Deployment Runbook

Current production deployment for Airco Secure V2. Architecture context lives in `CLAUDE.md`; this file is the operational flow.

## Production topology

| Layer | Provider | Purpose | Current endpoint |
|---|---|---|---|
| Frontend | Cloudflare Pages | React SPA | `https://the-airco-v2.pages.dev` / `https://app.the-airco.net` |
| CPU stack | Hostinger KVM 2 | API, Postgres, Redis, MinIO, Centrifugo, go2rtc, mediamtx, CPU consumers, Caddy | `72.61.239.69` / `100.103.80.105` |
| GPU stack | RunPod pod | Triton, Savant, identity pipeline, alternate Ultimate adapter | See `RUNPOD_POD_ID` in `/app/airco/.env.production` |
| Registry | GHCR | Private bootstrap + service images | `ghcr.io/the-airco-v2/airco-*` |
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
- `Backend/docker/**` does not rebuild from this workflow; the outer RunPod bootstrap image is published separately by `.github/workflows/build-runpod-bootstrap.yml`.

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

1. RunPod pulls the outer bootstrap image `ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest`
2. The image ENTRYPOINT writes logs to `/workspace/bootstrap.log`
3. The entrypoint starts Tailscale and joins the tailnet as `airco-gpu`
4. The entrypoint clones or pulls `/workspace/The-Airco-V2`
5. The entrypoint writes `Backend/.env.production` from pod env vars
6. The entrypoint logs into GHCR with `GHCR_PAT`, pulls the inner GPU images with Podman, and runs `podman-compose -f docker-compose.gpu.yml up -d`

The GPU pod uses Podman instead of Docker because RunPod community pods do not provide the privileges Docker needs for bridge networking and iptables.

Private GHCR setup for the outer bootstrap image:

1. Keep `airco-runpod-bootstrap` private in GitHub Packages.
2. In RunPod, create a container registry auth for GHCR using a GitHub username and PAT with package-read access.
3. Record the resulting `containerRegistryAuthId`.
4. Set `RUNPOD_CONTAINER_REGISTRY_AUTH_ID=<id>` in `Backend/.env.runpod`.
5. The script passes that ID to RunPod so the outer image pull succeeds before the container starts.
6. Set `AUTO_LINK_HOSTINGER=1` in `Backend/.env.runpod` for one-command cutover; the script updates `RUNPOD_POD_ID` on Hostinger and restarts the API after pod creation.

Recommended repo-native flow:

1. Copy `Backend/.env.runpod.template` to `Backend/.env.runpod`.
2. Fill in the RunPod, GHCR, Tailscale, and app secrets once.
3. Run:
   `python3 Backend/scripts/create_runpod_pod.py --env-file Backend/.env.runpod`

GitHub Actions flow:

1. Save the filled `Backend/.env.runpod` contents as the GitHub secret `RUNPOD_ENV_FILE`.
2. Ensure `SSH_PRIVATE_KEY` is configured for Hostinger access.
3. Run `.github/workflows/deploy-runpod-pod.yml`.
4. Leave `rebuild_bootstrap=true` when bootstrap image changes are included; set it to `false` only when reusing the already-published bootstrap image.

CI/CD split:

- `.github/workflows/build-images.yml` builds and deploys the CPU stack plus the normal service images.
- `.github/workflows/build-runpod-bootstrap.yml` rebuilds only the outer RunPod bootstrap image.
- `.github/workflows/deploy-runpod-pod.yml` is the end-to-end GPU pod creation workflow. It writes `Backend/.env.runpod` from `RUNPOD_ENV_FILE`, optionally rebuilds the bootstrap image, creates the pod, and links Hostinger automatically.

Notes:

- `GHCR_PAT` is still required as a pod env var even when `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` is set.
- `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` authenticates RunPod's outer image pull.
- `GHCR_PAT` authenticates the bootstrap container's `git clone` and inner `podman login ghcr.io`.
- `AUTO_LINK_HOSTINGER=1` makes `create_runpod_pod.py` update `/app/airco/.env.production` and restart `airco-api` over SSH. Override `HOSTINGER_SSH_TARGET`, `HOSTINGER_ENV_FILE`, or `HOSTINGER_APP_DIR` if your host layout changes.
- `Backend/.env.runpod` is local-only and ignored by git. `Backend/.env.runpod.template` is the checked-in template.

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
- Pod ID: see `RUNPOD_POD_ID` on Hostinger for the currently linked pod
- Outer image: `ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest`
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
- A private `airco-runpod-bootstrap` image requires `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` at pod creation time; `GHCR_PAT` alone is not enough for the initial RunPod pull.
