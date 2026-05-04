# Airco Secure V2

This README is for the standalone V2 stack rooted in `The-Airco/` only:

- frontend: `Frontend`
- backend/runtime: `Backend`

Ignore the older `frontend/`, `backend/`, and legacy runtime paths when working on V2.

## What V2 Runs

V2 local dev is split into:

- `Frontend` for the UI
- `Backend` for API, Redis, TimescaleDB, go2rtc, Centrifugo, MinIO, Savant, Triton, and the V2 consumers

## Prerequisites

- Docker with Compose
- NVIDIA Container Toolkit for GPU modes
- Node.js for `Frontend`
- `Backend/.env.local` present
- Optional: an `Ultimate-Tracker` checkout if you plan to use the alternate RE-ID engine path in `gpu-full`

Create the local env file if needed:

```bash
cd Backend
cp .env.local.example .env.local
```

Then fill in the required values in `Backend/.env.local`.

If your `Ultimate-Tracker` checkout lives somewhere else, set `ULTIMATE_POC_PATH` in `Backend/.env.local` to that location.

## Local Start

### 1. Frontend

Run this in a separate terminal:

```bash
cd Frontend
npm install
npm run dev
```

Frontend URL:

- `http://localhost:3000`

### 2. Backend / Runtime

Run this from `Backend/`.

API-only mode:

```bash
cd Backend
./local.sh
```

Full GPU mode:

```bash
cd Backend
./local.sh --gpu
```

GPU-lite mode:

```bash
cd Backend
./local.sh --gpu-lite
```

## Which Mode To Use

Use `./local.sh --gpu` when you want real V2 analytics behavior:

- Savant detector pipeline
- Triton identity models
- `identity-consumer`
- production-like anonymous-person / identity flow

Use `./local.sh --gpu-lite` only when you want a reduced local detector path.

Use `./local.sh` only when you are working on API or UI that does not need live analytics.

## Stop / Restart / Logs

Stop everything:

```bash
cd Backend
./local.sh --down
```

Tail logs:

```bash
cd Backend
./local.sh --logs
```

Run migrations only:

```bash
cd Backend
./local.sh --migrate
```

Clean restart for local GPU work:

```bash
cd Backend
./local.sh --down
./local.sh --gpu
```

## Local URLs

When V2 is up, these are the main endpoints:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- go2rtc: `http://localhost:1984`
- Centrifugo: `http://localhost:8088`
- MinIO Console: `http://localhost:9001`
- Triton health: `http://localhost:8002/v2/health/ready`

MinIO local credentials:

- user: `airco`
- password: `localdev`

## Quick Health Checks

Check running containers:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

Check the running V2 session:

```bash
docker exec v2-timescaledb-1 psql -U airco -d airco -Atc "select id, name, status from sessions order by created_at desc limit 5;"
```

Check latest live track events:

```bash
docker exec v2-redis-1 redis-cli XREVRANGE airco:tracks + - COUNT 10
```

## Current V2 Local Runtime Notes

As of `2026-04-04`, the local `gpu-full` runtime is working materially better than before:

- Triton identity models load
- Savant is healthy
- live track events flow
- canonical person creation is owned by `identity-consumer`

Important current caveat:

- selecting multiple cameras in a session does not yet mean multi-camera analytics is running
- the current local runtime still binds analysis to a single active camera stream
- session data may show 6 attached cameras, but live `airco:tracks` can still come from only the first selected camera until the multi-camera runtime work is completed

So if you are validating true multi-camera analytics, do not assume the current local stack already does that.

## Main Files To Know

- local startup: [local.sh](Backend/local.sh)
- local compose: [docker-compose.local.yml](Backend/docker-compose.local.yml)
- V2 API: [main.py](Backend/services/api/api/main.py)
- session control bridge: [main.py](Backend/services/session-control/session_control/main.py)
- Savant module: [module.yml](Backend/services/savant-pipeline/module.yml)
- frontend app shell: [router.tsx](Frontend/src/router.tsx)

## Useful Debug References

- [local-gpu-runtime-debug-2026-04-04.md](../docs/local-gpu-runtime-debug-2026-04-04.md)
- [debugging-analytics-not-working.md](../docs/debugging-analytics-not-working.md)
- [work_tracker.md](../docs/work_tracker.md)
