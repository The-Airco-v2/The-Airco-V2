# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

This repo is the **Airco Secure V2** standalone stack. Only `Backend/` and `Frontend/` are in scope. The README's note still applies: ignore any older `frontend/` / `backend/` / legacy runtime paths if they ever appear.

`Ultimate-Tracker/` is a sibling checkout, not a build target — it is mounted read-only into the `ultimate-adapter` container as the alternate REID engine. If a user has it elsewhere, point `ULTIMATE_POC_PATH` in `Backend/.env.local` at it.

## Common commands

### Backend — local dev (`Backend/`)

The wrapper around `docker compose` is `Backend/local.sh`. It picks the right compose profile and runs Alembic after bringing services up.

```bash
./local.sh                 # infra + API only (no GPU services)
./local.sh --gpu           # gpu profile: detector path only, no Triton, no identity-consumer
./local.sh --gpu-full      # via --gpu in script: full stack — see below
./local.sh --gpu           # → triggers gpu-full profile in script (full V2 stack incl. Triton + identity-consumer)
./local.sh --gpu-lite      # → triggers gpu profile (lite, person detector only)
./local.sh --down          # stop & remove containers across both profiles
./local.sh --migrate       # run alembic upgrade head only
./local.sh --logs          # tail logs (gpu-full profile)
```

Note: `--gpu` in the script maps to the `gpu-full` compose profile (not the `gpu` profile). `--gpu-lite` maps to the `gpu` profile. Don't change those mappings without reading both files. `Backend/.env.local` must exist (copy from `.env.local.example` and fill the three Supabase values).

Production-style commands live in `Backend/Makefile` and use `docker-compose.yml` + `.env.production`:

```bash
make up                    # bring up production compose
make migrate               # scripts/run_migrations.sh against $ENV_FILE
make seed                  # scripts/seed_cameras.py
make deploy-apps           # incremental rebuild of app services
make health-check          # scripts/health_check.sh
make log-<service>         # follow logs for one service
```

### Backend — tests

Pytest config is at `Backend/pytest.ini`; it sets `pythonpath = shared services/api` and `asyncio_mode = auto`. `tests/conftest.py` extends sys.path for the other service packages and stubs `pgvector` so a real Postgres is not required for unit tests.

```bash
cd Backend
python -m pip install -e "shared[test]"   # one-time
pytest tests -v                           # full suite (also: `make test`)
pytest tests/test_auth.py -v              # one file
pytest tests/test_auth.py::test_xyz -v    # one test
```

The `api_client` fixture in `conftest.py` wires a FastAPI `TestClient` with dependency overrides for auth and DB — use it instead of standing up a real DB.

### Frontend (`Frontend/`)

```bash
npm install
npm run dev          # vite on :3000, proxies /api → :8000, /webrtc → :1984
npm run build        # tsc -b && vite build
npm run test         # vitest run
npx vitest run src/hooks/useSessions.ts   # one file
```

The Vite proxy is the reason API calls in the SPA use plain `/api/...` paths rather than absolute URLs (see `Frontend/vite.config.ts`).

## Architecture

### The big picture

Backend is a **multi-service monorepo** where every runtime service is a separate Docker image but they all share one Python package, `shared/airco/`. Communication between services is exclusively via **Redis Streams** (pub/sub with consumer groups) — there are no direct service-to-service HTTP calls between consumers and the pipeline. Postgres (TimescaleDB + pgvector) is the durable store; MinIO holds evidence frames; Centrifugo fans out realtime updates to the SPA.

```
RTSP cameras
    │
    ▼
go2rtc ── (RTSP relay, stream "active_session" repointed by session-control)
    │
    ▼
savant-feeder ── ZMQ ──► savant-pipeline (GPU detector + tracker + phone + crops)
                                   │
                                   ▼
                         Redis Streams (airco:tracks, airco:crops, airco:phones, airco:snapshots, airco:identity, airco:alerts, airco:overview, airco:control)
                                   │
       ┌──────────────────┬────────┴──────────┬───────────────────┐
       ▼                  ▼                    ▼                   ▼
identity-consumer   analytics-consumer   snapshot-consumer    ws-publisher
(face/body match,   (alerts, scoring)    (frames → MinIO)     (Streams → Centrifugo)
 Triton, canonical
 persons, cross-cam
 merger)
                                   │
                                   ▼
                              FastAPI (api) ◄── browser SPA (Vite/React)
```

`ultimate-adapter` is an alternate path: when a session is started with `reid_profile = "ultimate"`, it consumes RTSP from go2rtc directly using the YOLO + OSNet stack mounted from the sibling `Ultimate-Tracker/` checkout, and produces the same Redis Streams events. Identity-consumer detects this profile and skips its own track-lifecycle work for that session (see `_session_uses_ultimate_path` in `services/identity-consumer/identity_consumer/main.py`).

### Compose profiles

`Backend/docker-compose.local.yml` uses two profiles to control which application services run. **Pick the right profile or services will silently not start:**

- **(no profile)** — `timescaledb`, `redis`, `minio`, `centrifugo`, `go2rtc`, `mediamtx`, `api`. Use for UI/API work that does not need analytics.
- **`gpu`** (lite) — adds `analytics-consumer`, `snapshot-consumer`, `session-control`, `ws-publisher`, `ultimate-adapter`, `savant-feeder`, `savant-pipeline-lite`. **No Triton, no identity-consumer.**
- **`gpu-full`** — adds everything in `gpu` plus `triton`, `identity-consumer`, and the full `savant-pipeline`. This is the production-equivalent path.

### Shared library (`Backend/shared/airco/`)

This is the single source of truth and the most important code in the repo:

- `models.py` — all SQLAlchemy ORM models. Three buckets: core/canonical (regular Postgres), time-series (TimescaleDB hypertables), embeddings (pgvector). Alembic's `target_metadata` is `airco.models.Base.metadata`, so adding a table here is what makes autogen work.
- `events.py` — Pydantic envelopes for every Redis Streams event. **`StreamNames` is the single source of truth for stream names** — never hardcode `"airco:tracks"` etc. anywhere else. `BaseEvent.to_redis()` / `from_redis()` is the (de)serialization contract; nested fields are JSON-encoded inside the flat string dict that XADD requires. `build_live_event_envelope` and `resolve_live_event_channel` define the shape and Centrifugo channel mapping for live events.
- `redis_streams.py` — `publish_event`, `consume_stream`, `consume_multiple_streams`. Consumer groups are conventionally named `<service>-group` (e.g. `identity-group`, `session-control-group`).
- `db.py` — `async_session` and `get_session` (FastAPI dependency).
- `config.py` — `pydantic-settings`-based settings.

Every service's `main.py` does `sys.path.insert(0, ".../shared")` near the top. That's how `airco.*` imports resolve when running outside Docker (e.g. tests, or `python -m`). Keep that pattern when adding new services.

### API service (`Backend/services/api/`)

FastAPI app at `api/main.py`. Routers under `/api/v2/...` are registered there; each router is in `api/routes/`. Auth is in `api/auth.py` — Supabase JWT verification via JWKS, plus a signed HMAC session cookie. Use `Depends(require_authenticated)` or `Depends(require_admin)` and read `auth.tenant_id` for tenant scoping (every tenant-scoped query filters by `tenant_id == auth.tenant_id`).

`go2rtc` stores its streams in memory only. The api `startup` hook re-registers every Camera row in go2rtc, and `session-control` re-points the `active_session` stream on each `session_start` event from `airco:control`. If you change camera routing logic, both of those flows need to keep working.

### Frontend (`Frontend/`)

React 18 + Vite + TypeScript. State: TanStack Query for server cache, React Context for auth, Centrifugo client for realtime. Routes: `src/router.tsx` (top-level), pages under `src/routes/app/` (authenticated) and `src/routes/auth/` (public). UI primitives are Radix + shadcn-style components in `src/components/ui/`. The `@/` alias maps to `src/`.

API calls go through `src/lib/api.ts` (`apiFetch`/`apiFetchJson`). On 401 it dispatches `auth:expired`, which `AuthProvider` handles by transitioning to unauthenticated. Keep new fetches on this helper so error parsing and the 401 hook stay consistent.

Realtime hooks (`useLive*`) subscribe to Centrifugo channels named per `resolve_live_event_channel` in `airco/events.py` — `tenant:<tenant>:overview`, `alerts:<session>`, `sessions:<session>`. If you change the backend mapping, change both sides together.

## Conventions worth knowing

- **Don't create new Redis stream names ad hoc.** Add them to `StreamNames` and import from there.
- **Tenant scoping is mandatory** on any new endpoint that touches Cameras, Sessions, Persons, Employees, Alerts, etc. Filter on `tenant_id` and rely on `require_authenticated`/`require_admin`.
- **Session config carries `reid_profile`** (`standard` or `ultimate`). Legacy value `ultimate_reid` normalizes to `ultimate` (see `_normalize_reid_profile` in `sessions.py` and `_normalize_selector` in identity-consumer). Preserve both spellings when matching user input; emit only the new value.
- **Migrations**: Alembic lives at `Backend/migrations/`. After editing models, generate a revision and verify it imports cleanly before committing. `--gpu`, `--gpu-lite`, and the no-arg local mode all run `alembic upgrade head` after `up`.
- **Tests don't require pgvector or Postgres.** `tests/conftest.py` stubs `pgvector` and patches `create_async_engine`. Don't add real DB calls into tests that use the `api_client` fixture — use the existing dependency overrides and `db_session_mock`.

## Key files to read first when starting a task

- `Backend/services/api/api/main.py` — API surface and router map
- `Backend/shared/airco/events.py` — event contracts and stream names
- `Backend/shared/airco/models.py` — database schema
- `Backend/services/session-control/session_control/main.py` — go2rtc/session lifecycle bridge
- `Backend/services/identity-consumer/identity_consumer/main.py` — identity pipeline entry point
- `Backend/docker-compose.local.yml` — service topology and compose profiles
- `Frontend/src/router.tsx` — page map
- `Frontend/src/lib/api.ts` and `src/lib/auth.tsx` — fetch + auth contract

## Deployment (production)

The production stack is split across three environments. Everything is wired over
Tailscale; no VPN or bastion is needed between the VPS and the GPU pod.

### 1 — Hostinger KVM 2 VPS (always-on CPU stack)

| Property | Value |
|---|---|
| **Public IP** | `72.61.239.69` |
| **Tailscale IP** | `100.103.80.105` |
| **Tailscale hostname** | `airco-hub` (registered as `srv1696728` in console) |
| **SSH** | `ssh -i ~/.ssh/id_ed25519 root@72.61.239.69` |
| **App files** | `/app/airco/` |
| **Env file** | `/app/airco/.env.production` |

**Services running** (via `docker-compose.cpu.yml` + `docker-compose.proxy.yml`):

- TimescaleDB (Postgres + pgvector) — port 5432
- Redis — port 6379
- MinIO — port 9000
- Centrifugo — port 8088
- FastAPI gateway (`airco-api`) — port 8000
- go2rtc — RTSP relay on port 8554, HTTP on port 1984
- mediamtx — port 8890
- Caddy (TLS terminator) — ports 80 / 443
- analytics-consumer, snapshot-consumer, session-control, ws-publisher

**Useful VPS commands:**

```bash
# Check all services
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml -f docker-compose.proxy.yml ps'

# Tail API logs
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml logs -f api'

# Restart a service
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'

# Check Tailscale peers (should show airco-gpu when GPU pod is running)
ssh root@72.61.239.69 'tailscale status'
```

**Volume-mount hotfix:** `gpu_controller.py` is bind-mounted from
`/app/airco/gpu_controller.py` into the API container so it can be updated
without rebuilding the image. Container version wins over repo version.

### 2 — RunPod GPU pod (on-demand, Community Cloud)

| Property | Value |
|---|---|
| **Base image** | `ubuntu:22.04` |
| **GPU** | NVIDIA RTX 3090 (Community Cloud) |
| **Tailscale hostname** | `airco-gpu` |
| **Persistent volume** | `/workspace` (40 GB, persists across stop/start) |
| **Container disk** | 40 GB |
| **Env file** | `/workspace/The-Airco-V2/Backend/.env.production` (written from pod env vars on startup) |
| **Bootstrap log** | `/workspace/bootstrap.log` |

**How the pod is provisioned:**

The pod is created once and reused. `RUNPOD_POD_ID` in `/app/airco/.env.production`
on the VPS tells `gpu_controller.py` which pod to `resume` and `stop`.

The pod's `dockerArgs` starts with `bash -c "..."` (ubuntu:22.04 has no ENTRYPOINT;
RunPod uses dockerArgs as CMD override, so a shell wrapper is required for `&&` / `|`
operators to work). The script:

1. Installs `curl`, `git`
2. Installs Tailscale, connects as `airco-gpu`
3. Clones / pulls `The-Airco-V2` repo into `/workspace/The-Airco-V2`
4. Runs `Backend/scripts/gpu_bootstrap.sh` which:
   - Installs **Podman** + `podman-compose` (used instead of Docker — avoids
     iptables/CAP_NET_ADMIN limitations on community pods)
   - Installs NVIDIA Container Toolkit, generates CDI spec for GPU access
   - Logs into GHCR, pulls GPU compose images
   - Starts the full GPU stack via `podman-compose -f docker-compose.gpu.yml up -d`
   - Runs `sleep infinity` to keep the container alive

**Why Podman instead of Docker:**
RunPod community pods lack `CAP_NET_ADMIN` and privileged access. `dockerd`
requires these for bridge networks and iptables. Podman uses `slirp4netns` for
networking and CDI for GPU access — neither requires elevated capabilities.

**Services running on GPU pod** (via `docker-compose.gpu.yml`):

- `triton` — NVIDIA Triton Inference Server (ports 8001/8002)
- `savant-pipeline` — DeepStream GPU detector + tracker
- `savant-feeder` — RTSP feed ingestion
- `identity-consumer` — Face/body ReID pipeline
- `analytics-consumer` — Alerts and scoring
- `snapshot-consumer` — Evidence frame writer to MinIO
- `session-control` — go2rtc stream routing bridge
- `ws-publisher` — Redis Streams → Centrifugo fan-out
- `ultimate-adapter` — Alternate YOLO+OSNet ReID path

All GPU services reach Postgres, Redis, MinIO, Centrifugo on the Hostinger VPS
via `AIRCO_HUB_HOST=100.103.80.105` (VPS Tailscale IP).

**GPU lifecycle (session-triggered):**

- Session `start` → `gpu_controller.py:ensure_running()` calls `podResume` GraphQL
  mutation, then polls `GPU_HEALTH_TARGET` URL until Triton reports ready.
- Session `stop` + idle timeout → `gpu_controller.py:release_if_idle()` calls
  `podStop` mutation (pod parked, volume retained, billing stops).
- Controller is a no-op when `RUNPOD_API_KEY` / `RUNPOD_POD_ID` are not set.
- `GPU_IDLE_TIMEOUT_SECONDS` (default 900) — seconds of zero active sessions before auto-stop.
- `GPU_BOOT_TIMEOUT_SECONDS` (default 180) — max seconds to wait for pod to become healthy.
- `GPU_HEALTH_TARGET` — HTTP URL polled to verify GPU pod is ready (e.g. Triton readiness endpoint).

**To create a new pod** (when the existing one is terminated / lost):

```bash
# Run the deploy script (kept in antigravity scratch dir, secrets included):
python3 /path/to/deploy_gpu_v3.py
# It tries RTX 3090, 4090, A6000, A5000, 3080Ti, A4000 in order.
# Copy the printed RUNPOD_POD_ID into /app/airco/.env.production, then restart the API:
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'
```

**To SSH into the GPU pod:**

```bash
# Via VPS (always works while Tailscale is up):
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 \
  "ssh -o StrictHostKeyChecking=no root@<airco-gpu-tailscale-ip>"
# Get the Tailscale IP from: ssh root@72.61.239.69 'tailscale status'
# Check bootstrap progress:  cat /workspace/bootstrap.log
# Check containers:          podman ps
# Check GPU:                 nvidia-smi
```

### 3 — Cloudflare Pages (frontend SPA)

| Property | Value |
|---|---|
| **Production URL** | `https://the-airco-v2.pages.dev` |
| **Custom domain** | `app.the-airco.net` (when DNS configured) |
| **Build root** | `Frontend/` |
| **Build command** | `npm run build` |
| **Output dir** | `dist` |

Env vars set in Cloudflare Pages dashboard:
- `VITE_API_URL=https://api.the-airco.net`
- `VITE_WS_URL=wss://api.the-airco.net/centrifugo/connection/websocket`
- `VITE_GO2RTC_URL=https://media.the-airco.net`

`Frontend/public/_redirects` — SPA fallback.
`Frontend/public/_headers` — security headers + asset cache.

### Cross-environment networking

```
Browser (Cloudflare Pages)
    │ HTTPS/WSS
    ▼
Caddy (Hostinger :443)
    │ reverse-proxy
    ▼
FastAPI api:8000 ──► Redis ──► streams ──────────────────┐
                     Centrifugo:8088                      │ Tailscale VPN
                                                          ▼
                                               RunPod GPU pod (airco-gpu)
                                               podman-compose GPU services
                                               consuming Redis Streams,
                                               writing to Postgres/MinIO
```

### Tailscale configuration

Both the Hostinger VPS and the RunPod pod authenticate to the same Tailscale
network. The VPS runs persistent `tailscaled` via system package. The GPU pod
runs `tailscaled --state=/workspace/tailscale.state` so the state persists on
the `/workspace` volume across pod stop/start (same node identity, no re-auth).

Auth key: `tskey-auth-kCi9o6yMF211CNTRL-...` — stored in
`TAILSCALE_AUTHKEY` in `/app/airco/.env.production` (VPS) and hardcoded in
`dockerArgs` of the RunPod pod definition.

### Image registry

All service images: `ghcr.io/the-airco-v2/airco-*`

CI: `.github/workflows/build-images.yml` builds on push to `main` or `deploy/**`.
Tags: `:sha-<short>` (always) and `:latest` (on main/deploy branches).

GPU model artifacts baked into images:
- `airco-triton` — ONNX + pre-built TRT plan files (rebuild per-GPU on first
  start, cached in `/workspace` across pod stop/start)
- `airco-savant-pipeline` — YOLO and SCRFD models under `/models/`
- `airco-ultimate-adapter` — Ultimate-Tracker weights under `/ultimate-poc/`

### Reverse proxy + TLS

`deploy/Caddyfile` + `docker-compose.proxy.yml` — Caddy as TLS terminator:

- `api.the-airco.net/*` → `api:8000`
- `api.the-airco.net/centrifugo/*` → `centrifugo:8088` (WebSocket)
- `api.the-airco.net/minio/*` → `minio:9000`
- `media.the-airco.net/*` → `go2rtc:1984`

Caddy auto-renews via Let's Encrypt. DNS A records must point to `72.61.239.69`.

### Cross-domain auth

Frontend on `app.the-airco.net` (Cloudflare), API on `api.the-airco.net`
(Hostinger). Session cookie must be parent-domain-scoped:

- `SESSION_COOKIE_DOMAIN=.the-airco.net`
- `SESSION_SECURE_COOKIE=true`
- `SESSION_SAME_SITE=none`

CORS allowlist in `api/main.py` covers production hosts; extras via
`CORS_EXTRA_ORIGINS` (comma-separated).

### Production env file

`Backend/.env.production.template` is the authoritative list. Copy to
`/app/airco/.env.production` on the Hostinger VPS and fill `__SET_ME__`
placeholders before bringing up the compose stack.



