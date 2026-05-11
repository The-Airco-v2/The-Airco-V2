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

The production stack splits across three environments:

- **Hostinger KVM 2 Singapore (always on)** — runs the CPU half: Postgres, Redis, MinIO, Centrifugo, FastAPI, go2rtc, mediamtx. Configured via `Backend/docker-compose.cpu.yml` + `.env.production`.
- **RunPod GPU pod (Community Cloud RTX 3090, on demand)** — runs the GPU half: Triton, savant-pipeline, savant-feeder, identity-consumer, analytics-consumer, snapshot-consumer, session-control, ws-publisher, ultimate-adapter. Configured via `Backend/docker-compose.gpu.yml`. The API resumes/stops the pod on session_start/session_stop.
- **Cloudflare Pages** — hosts the Vite-built frontend. `Frontend/.env.production.example` documents the three required `VITE_*` URLs.

### Cross-environment networking

The Hostinger CPU host and RunPod pod talk via Tailscale (free tier). The Hostinger box advertises itself on the tailnet as `airco-hub`; GPU compose reads `AIRCO_HUB_HOST` to find Postgres / Redis / MinIO / Centrifugo / go2rtc.

### Image registry

All service images live in `ghcr.io/the-airco-v2/airco-*`. `.github/workflows/build-images.yml` builds and pushes on every push to `main` or `deploy/**`. Tags: `:sha-<short>` (always) and `:latest` (on main/deploy branches).

GPU model artifacts are baked into the images:
- `airco-triton` includes ONNX + any pre-built `.plan` engines. TRT engines rebuild per-GPU on first start and persist on RunPod's container filesystem across stop/start.
- `airco-savant-pipeline` includes the YOLO and SCRFD ONNX/PT under `/models/`.
- `airco-ultimate-adapter` includes the Ultimate-Tracker weights under `/ultimate-poc/` (CI vendors them from the sibling `Ultimate-Tracker/` directory before docker build).

### GPU boot controller

`services/api/api/runpod_client.py` + `services/api/api/gpu_controller.py` resume / stop the RunPod pod via the RunPod GraphQL API. `start_session` returns 202 with `status: "starting"` when the controller is enabled, then a BackgroundTask boots the pod, polls the configured `GPU_HEALTH_TARGET` URL, and publishes `session_start` to `airco:control`. A startup task (`start_gpu_idle_loop` in `api/main.py`) stops the pod after `GPU_IDLE_TIMEOUT_SECONDS` of zero active sessions. The controller is a no-op when `RUNPOD_API_KEY` / `RUNPOD_POD_ID` aren't set, so local dev works unchanged.

### Reverse proxy + TLS

`deploy/Caddyfile` + `docker-compose.proxy.yml` add Caddy as a TLS terminator on the Hostinger host. Routes:
- `api.the-airco.net/*` → `api:8000`
- `api.the-airco.net/centrifugo/*` → `centrifugo:8088` (WebSocket)
- `api.the-airco.net/minio/*` → `minio:9000`
- `media.the-airco.net/*` → `go2rtc:1984`

Caddy auto-renews via Let's Encrypt. DNS must point at the Hostinger VPS public IP before TLS issuance can succeed.

### Cross-domain auth

Because the frontend lives on `app.the-airco.net` (Cloudflare) and the API on `api.the-airco.net` (Hostinger), the session cookie must be parent-domain-scoped. Set:
- `SESSION_COOKIE_DOMAIN=.the-airco.net`
- `SESSION_SECURE_COOKIE=true`
- `SESSION_SAME_SITE=none`

The CORS allowlist in `api/main.py` includes the production hosts by default; additional origins go in `CORS_EXTRA_ORIGINS` (comma-separated).

### Cloudflare Pages build settings

When connecting the repo to Cloudflare Pages:
- **Root directory**: `Frontend`
- **Build command**: `npm run build`
- **Output directory**: `dist`
- **Environment variables**: from `Frontend/.env.production.example` (`VITE_API_URL`, `VITE_WS_URL`, `VITE_GO2RTC_URL`)

`Frontend/public/_redirects` provides the SPA fallback; `Frontend/public/_headers` sets security headers and asset-cache hints.

### Production env file

`Backend/.env.production.template` is the authoritative list of required vars. Always copy to `.env.production` on the Hostinger host and fill placeholders (`__SET_ME__`) before bringing up the compose stack.
