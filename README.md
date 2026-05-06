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


Big Picture
This repo is V2 of the system.

The root README.md explicitly says:

Use Backend/ and Frontend/
Ignore the older legacy backend/ and frontend/ paths
The whole project is a camera-based workforce/identity intelligence platform:

Frontend = dashboard, live view, sessions, identity review, reports
Backend API = auth, CRUD, session control, MinIO URLs, identity review, analytics
GPU runtime = Savant pipeline, Triton, Ultimate adapter, consumers
Storage / infra = PostgreSQL/TimescaleDB, Redis, MinIO, go2rtc, Centrifugo, Supabase
Top-Level File Structure
The-Airco/
README.md
The main architecture and startup guide for V2.
Explains local URLs, run modes, and which files matter.
start.bat
Windows launcher for bringing up backend + frontend.
Backend/
All backend/runtime orchestration.
Docker Compose files, Python services, shared libraries, configs, tests.
Frontend/
Vite + React app.
Ultimate-Tracker/
External model assets used by the Ultimate RE-ID path.
Contains large model files like yolo11s.pt and osnet_x1_0_msmt17.pth.
Backend Structure
Backend/docker-compose.local.yml
This is the local dev runtime.

It wires together:

TimescaleDB
Redis
MinIO
Centrifugo
go2rtc
mediamtx
API
Optional GPU services:
triton
identity-consumer
analytics-consumer
snapshot-consumer
session-control
ws-publisher
ultimate-adapter
savant-pipeline
savant-feeder
It also defines the shared env vars for:

POSTGRES_*
REDIS_URL
MINIO_*
CENTRIFUGO_*
TRITON_URL
SUPABASE_*
Backend/docker-compose.yml
This is the more production-like compose file.

It includes extra pieces like:

nginx
frontend container
portainer
So the local compose is for development, while the main compose is more “full stack”.

Backend/local.sh and Backend/local.bat
These are the startup scripts.

local.sh
Main orchestration script.
Starts/stops compose stacks and supports modes like:
API-only
GPU-lite
GPU-full
local.bat
Windows wrapper that calls the bash script.
This is the “how do I actually run the stack?” layer.

Backend/config/
This folder contains runtime config files.

go2rtc.yml
Defines streams and the active_session relay stream.
Critical for live video.
centrifugo.json
Centrifugo configuration.
Holds admin password and server settings.
timescaledb/init.sql
DB bootstrap/init script mounted into the database container.
Used to initialize extensions/schema on first start.
Backend/shared/airco/
This is the shared backend core.
Many services import from here.

config.py
Central settings object.
Reads environment variables.
Defines:
Postgres/TimescaleDB connection
Redis URL
MinIO endpoint and bucket
Centrifugo API config
Triton URL
Supabase URL/keys
session cookie settings
This is the configuration backbone.

db.py
Creates the async SQLAlchemy engine.
Builds async_session.
All backend services use this to talk to the database.
models.py
This is one of the most important files.

It defines the entire database schema.

Core tables
Tenant
Camera
CameraPair
CameraZone
Employee
EmployeeFaceTemplate
Session tables
Session
SessionCamera
Canonical identity tables
SessionPerson
IdentityCluster
IdentityClusterMember
IdentityMergeReview
SessionPersonTrackBinding
PersonEmbedding
Time-series tables
TrackEvent
FaceObservation
IdentityEvent
PhoneEvent
ActivityEvent
AttendanceEvent
Mutable/state tables
CameraPresenceSegment
Alert
Snapshot
ReviewTask
events.py
This is the event contract for Redis Streams.

It defines:

Stream names:
airco:tracks
airco:crops
airco:phones
airco:identity
airco:snapshots
airco:alerts
airco:overview
And typed payloads like:

TrackEventPayload
CropEventPayload
PhoneEventPayload
IdentityEventPayload
SnapshotEventPayload
AlertEventPayload
This file is the message schema contract between services.

redis_streams.py
A small helper layer for Redis Streams:

publish_event
consume_stream
consume_multiple_streams
consumer group creation
This is how services communicate asynchronously.

minio_client.py
Handles MinIO:

get_minio()
upload_bytes()
get_presigned_url()
Important idea:

The DB stores object names / paths
The API turns them into browser-accessible URLs
The frontend only renders <img src="...">
authority.py
This is the Supabase authority lookup seam.

It queries Supabase REST for:

user_profiles
tenants
Used by auth to determine:

role
tenant assignment
active/inactive state
auth.py
This is the auth brain.

It handles:

Supabase password login
JWT verification
backend session cookie signing
session decoding
tenant/profile validation
require_authenticated
require_admin
This is how the frontend login becomes a secure backend session.

API Service Structure
Backend/services/api/api/main.py
This is the API entrypoint.

It:

creates the FastAPI app
enables CORS
mounts all routers
syncs cameras to go2rtc on startup
handles validation and integrity errors
Important startup behavior:

go2rtc keeps streams in memory
on API startup, all cameras are re-registered into go2rtc
That prevents live streams from disappearing after restarts.

Backend/services/api/api/routes/
These are the HTTP endpoints the frontend uses.

auth.py
POST /api/auth/login
POST /api/auth/logout
GET /api/auth/me
It:

logs in via Supabase
sets the backend session cookie
returns account state to the frontend
sessions.py
This manages sessions.

It:

creates sessions
lists sessions
starts/stops/pauses/resumes sessions
exposes ultimate runtime status
Most important behavior:

start_session() publishes a session_start event to Redis stream airco:control
stop_session() publishes session_stop
pause_session() publishes session_pause
That is how runtime services learn a session has changed.

cameras.py
This manages cameras.

It:

registers cameras in the DB
deletes cameras
syncs them to go2rtc
When you add a camera, the backend calls go2rtc so the stream becomes available.

persons.py
This powers the unknown-person / canonical person views.

It:

lists session persons
returns unknown-person dashboards
builds detailed evidence payloads
normalizes MinIO image URLs
This file is one of the main links between the DB, MinIO, and frontend review UI.

identity_reviews.py
This powers human review actions.

It:

returns review queue items
returns review item detail
returns review history
merges unknown persons
assigns a person to an employee
undoes a review
identity_review_service.py
This is the domain logic behind the review routes.

It:

loads unknown people
builds queue items
creates / merges identity clusters
records audit reviews
undoes merges and assignments
This is the “business logic” layer for identity review.

person_evidence.py
This builds the evidence timeline.

It:

selects representative snapshots
creates first-seen / last-seen / camera-transition / zone-transition moments
inserts dwell checkpoints
builds storyboard/timeline data
This is why the identity review UI can show a structured evidence story instead of random frames.

centrifugo_proxy.py
This issues Centrifugo websocket tokens.

Frontend uses this to connect to live updates securely.

analytics.py
This builds higher-level analytics views like:

employee history
cross-session trends
It reads from the DB tables materialized by the consumers.

Backend Runtime / GPU Services
Backend/services/session-control/session_control/main.py
This is the bridge between session events and go2rtc.

It listens to airco:control and:

sets Redis keys for active session/camera IDs
updates go2rtc active_session
registers per-session camera relay streams
restores the last active session after restart
This service is the glue that keeps “current session video” alive.

Backend/services/savant-feeder/savant_feeder/main.py
This service reads the active session/cameras from Redis and:

pulls RTSP from go2rtc
decodes frames with OpenCV
pushes JPEG frames into Savant over ZeroMQ
So it is the frame feeder into the GPU pipeline.

runtime.py
Pure helpers for:

stream naming
RTSP URL construction
parsing active camera IDs
Backend/services/savant-pipeline/
This is the DeepStream/Savant GPU pipeline.

module.yml
The main pipeline definition.

It does:

source from ZeroMQ
person detector
nvtracker
phone detector
custom Python post-processing
Redis stream sink
module.local.yml
A lighter local variant.

Used when you want reduced GPU load.

tracker.yml
Tracker configuration for DeepStream low-level tracker.

This is where the NvTrackerParamsBase warnings came from when parameters were in the wrong place or had wrong casing.

custom_pyfunc.py
Post-processing inside the pipeline.

It:

extracts body crops
extracts face crops
optionally uses SCRFD
creates periodic snapshots
stores frame-local artifacts for the sink
redis_sink.py
This is the bridge out of Savant.

It publishes to Redis streams:

airco:tracks
airco:crops
airco:phones
airco:snapshots
event_utils.py
Helpers for:

filtering invalid track IDs
ignoring full-frame detections
frame_artifacts.py
A small shared buffer between the two pyfunc stages.

It lets crop/snapshot artifacts move from extractor to sink.

models/
Contains detector assets used by the Savant pipeline.

Backend/services/identity-consumer/
This service reads tracks and crops from Redis and builds canonical identity state.

main.py
It consumes:

airco:tracks
airco:crops
And uses:

Triton embeddings
PersonManager
FaceMatcher
cross-camera merging
It updates:

SessionPerson
IdentityCluster
SessionPersonTrackBinding
PersonEmbedding
This is the service that turns raw detections into “this is a person”.

Backend/services/analytics-consumer/
This service turns raw events into business analytics.

It consumes:

airco:tracks
airco:identity
airco:phones
And materializes:

dwell time
attendance
alerts
phone usage
activity
It writes those into the DB tables used by dashboards and reports.

Backend/services/snapshot-consumer/
This service creates evidence images.

It:

decodes snapshot payloads
renders bounding boxes
uploads JPEGs to MinIO
stores snapshot rows in DB
updates SessionPerson.best_thumbnail_url
renderer.py
Draws red boxes and labels onto snapshot images.

This is the actual visual evidence generation path.

Backend/services/ws-publisher/
This bridges Redis Streams to Centrifugo.

main.py
Consumes live event streams.

publisher.py
Builds the live event envelope and publishes it to Centrifugo.

This is what makes the UI update in real time.

Backend/services/triton/
This is the Triton model repository.

models/README.md
Explains the model contract.

Important points:

runtime configs live in config.pbtxt
artifacts like model.plan are expected in each model folder
the main served identity models are:
arcface
osnet
Savant’s detector artifacts live elsewhere.

Triton is used mainly for embedding extraction and re-ID features.

Backend/services/ultimate-adapter/
This is the alternate RE-ID engine path.

It is the “ultimate” runtime.

main.py
Starts:

control consumer
runtime supervisor
service_runtime.py
Owns the runtime loop.

It:

reads active runtime context from Redis
starts/stops workers per active camera
opens RTSP from go2rtc
processes frames
publishes runtime status
runtime.py
Thin orchestration layer that:

processes a frame
publishes outputs
performs cleanup/shutdown
output_adapter.py
Converts Ultimate internal results back into the same V2 event contracts:

track events
crops
snapshots
identity events
id_bridge.py
Maps Ultimate global IDs to V2 SessionPerson rows.

This is how the Ultimate path still fits the same database model.

ultimate_core/
The internal tracker/re-id implementation:

tracker.py
registry.py
features.py
motion.py
gallery.py
identity.py
codec.py
bundle.py
birth.py
facade.py
This is the “engine room” of the ultimate RE-ID path.

Database: PostgreSQL vs TimescaleDB
What the project really uses
The runtime uses TimescaleDB, which is PostgreSQL with time-series extensions.

So practically:

PostgreSQL = the relational database layer
TimescaleDB = the actual server image used in Docker
same DB instance, but different table categories
How tables are used
From shared/airco/models.py:

Core relational tables
tenants
cameras
employees
sessions
Canonical identity tables
session_persons
identity_clusters
track bindings
Time-series/event tables
track_events
face_observations
identity_events
phone_events
activity_events
attendance_events
Mutable state tables
alerts
snapshots
review tasks
camera presence segments
The key design is:

events are appended
state is materialized from events
SessionPerson is the canonical source of truth for a physical person in a session
MinIO
What MinIO is used for
MinIO stores image evidence:

full-frame snapshots
face crops
body crops
thumbnails
How it works
snapshot-consumer uploads JPEG bytes to MinIO
the DB stores the object path in Snapshot.full_frame_url
API route helpers convert that path into a browser-accessible URL
frontend renders it directly in <img src="...">
Why it matters
The frontend does not create MinIO URLs itself.
The backend must return a URL the browser can actually open.

Supabase
What Supabase does here
Supabase is not the main analytics DB.

It is used for:

password login
JWT verification
user profile lookup
tenant lookup
The flow
Frontend submits email/password to /api/auth/login
Backend calls Supabase password auth
Backend checks:
user profile
tenant active state
Backend sets its own signed session cookie
Frontend uses that cookie for subsequent API calls
So Supabase is the authority for identity and access, not the main event store.

go2rtc
What go2rtc does
go2rtc is the stream relay / live video bridge.

It helps with:

camera RTSP relay
active session stream alias
WebRTC/MSE/HLS/MJPEG access for the browser
How it is used
API registers cameras into go2rtc on startup and on camera create
session-control updates the active_session relay
savant-feeder and ultimate-adapter pull from go2rtc RTSP aliases
frontend live view embeds go2rtc/stream.html
So go2rtc is the live video infrastructure backbone.

Centrifugo
What Centrifugo does
Centrifugo is the real-time websocket hub.

How it connects
backend services publish events into Redis Streams
ws-publisher consumes them
ws-publisher sends normalized live events to Centrifugo
frontend subscribes through lib/live/client.ts
backend issues connection tokens from /api/v2/ws/token
Channels
The live event system uses channels such as:

tenant:<tenant_id>:overview
sessions:<session_id>
alerts:<session_id>
This is how dashboard numbers and alerts update in real time.

Frontend Structure
Frontend/package.json
This is a Vite + React + TypeScript app.

Important dependencies:

React
React Router
TanStack React Query
Centrifuge
Radix UI
Tailwind
Framer Motion
Sonner
Lucide icons
Frontend/src/main.tsx
App bootstrap:

creates React Query client
wraps app in AuthProvider
installs router
mounts toaster
This is the root of the UI.

Frontend/src/router.tsx
Defines the app routes.

Main app routes
/dashboard
/cameras
/live
/reports
/identity-review
/employees
/sessions
/alerts
Auth routes
/login
/account-not-provisioned
/account-inactive
/inactive-tenant
Frontend/src/lib/api.ts
This is the API fetch wrapper.

It:

includes credentials
handles JSON parsing
normalizes API errors
dispatches auth:expired on 401
This is the frontend’s common API layer.

Frontend/src/lib/auth.tsx
This manages auth state.

It:

calls /api/auth/me
maps backend account states to frontend auth states
stores user info
exposes refresh() and logout()
This is the frontend’s auth brain.

Frontend/src/lib/live/client.ts
This is the Centrifugo client.

It:

resolves websocket URL
gets a token from /api/v2/ws/token
subscribes to channels
validates live payload shape
delivers updates to listeners
This is how live backend events reach React Query caches.

Frontend/src/hooks/
These are the data-fetching hooks.

useSessions.ts
fetches sessions
starts/stops/creates sessions
reads ultimate runtime status
also contains live-cache merging helpers
useIdentityReviewPage.ts
fetches identity review queue/item/history
usePersons.ts
fetches persons and unknown-person evidence
useAlerts.ts
fetches alerts and acknowledge mutation
useCameras.ts
fetches cameras
There are also test files for live merge behavior and cache freshness.

Frontend/src/components/
components/layout/
Sidebar.tsx
Topbar.tsx
These form the app shell.

components/shared/
PageHeader.tsx
StatusBadge.tsx
KpiCard.tsx
Reusable UI pieces.

components/identity/
IdentityReviewDialog.tsx
The modal used to merge unknown people or assign them to employees.

components/ui/
Radix/shadcn-style base UI primitives
Frontend/src/routes/app/
Main pages:

dashboard.tsx
cameras.tsx
live.tsx
reports.tsx
identity-review.tsx
employees.tsx
sessions.tsx
alerts.tsx
dashboard.tsx
The main operations overview.

It combines:

overview metrics
alerts
live employee intelligence
unknown-person evidence
identity-review.tsx
The review workspace.

It shows:

queue items
evidence detail
candidate comparisons
merge/assign/undo actions
history
sessions.tsx
Session control UI.

It lets you:

create sessions
start/stop sessions
choose standard vs ultimate re-ID mode
inspect ultimate runtime status
live.tsx
Live camera grid.

It embeds go2rtc player URLs in iframes.

Frontend/src/routes/auth/
login.tsx
account-not-provisioned.tsx
inactive-user / inactive-tenant style pages
login.tsx
This is the login form.

It:

submits to /api/auth/login
shows auth-state-specific errors
refreshes auth state after success
redirects into the app
Frontend/src/types/index.ts
This is the frontend contract mirror.

It defines the shared API shapes for:

auth
cameras
sessions
overview
alerts
employee intelligence
unknown persons
identity review
reports
runtime status
This file is the type-level contract between UI and backend.

How Everything Works Together
Here’s the full runtime loop:

text
1. User logs in
   Frontend -> Backend /api/auth/login -> Supabase -> backend session cookie
 
2. User starts a session
   Frontend -> Backend /api/v2/sessions/{id}/start
   Backend writes session status in DB and publishes airco:control
 
3. session-control reacts
   Reads airco:control
   Updates Redis active session keys
   Registers go2rtc relay streams
 
4. savant-feeder or ultimate-adapter consume live video
   Pull RTSP from go2rtc
   Push frames into GPU pipeline / runtime
 
5. GPU pipeline processes frames
   Savant detects persons / crops / phones / snapshots
 
6. Events are published to Redis Streams
   tracks, crops, phones, snapshots, identity, alerts
 
7. Consumers materialize state
   - identity-consumer -> SessionPerson / clusters / bindings
   - analytics-consumer -> dwell / attendance / alerts
   - snapshot-consumer -> MinIO images + Snapshot DB rows
   - ws-publisher -> Centrifugo live events
 
8. Frontend updates
   API for static data
   Centrifugo for live updates
   MinIO image URLs for evidence thumbnails
Key Design Ideas You Should Remember
SessionPerson is the canonical person record
Redis Streams are the main event bus
MinIO stores evidence images, not the DB
Supabase handles auth/authority, not analytics storage
go2rtc is the video relay layer
Centrifugo is the live update layer
Savant / Ultimate / Triton are the GPU inference layers
Frontend mostly renders data and subscribes to live updates