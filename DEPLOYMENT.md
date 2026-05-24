# Deployment Status & Runbook

Living document. Read top-down for "where are we, what's next." Architecture context is in CLAUDE.md.

## Stack

| Layer | Provider | Cost/mo | Status |
|---|---|---|---|
| Frontend | Cloudflare Pages | $0 | ✅ Deployed (`https://the-airco-v2.pages.dev`) |
| API + CPU services | **Hostinger KVM 2 Singapore** | ~$9–12 | ✅ Running (VPS: `72.61.239.69`, Tailscale: `100.103.80.105`) |
| GPU on demand | RunPod Community RTX A4000 | ~$0.30/hr (when running) + ~$5/mo storage | ✅ Deployed (Pod ID: `an5cwp48emcmjj`) |
| Image registry | **GHCR — `ghcr.io/the-airco-v2/`** | $0 (private images) | ✅ Working |
| Inter-host networking | Tailscale free tier | $0 | ✅ Connected (`airco-hub` and `airco-gpu`) |
| DNS + TLS | Cloudflare DNS + Caddy on the CPU host | $0 | ✅ Active (`api.the-airco.net`) |

Repo: `github.com/The-Airco-v2/The-Airco-V2` (transferred from `dscyrus07-dev`)
Domain: `the-airco.net`
Branch with deploy code: `main` (merged)

## Phase tracker

| Phase | What | Status |
|---|---|---|
| 0 | Pre-flight (accounts, billing, secrets) | ✅ Done |
| 1 | Code changes land in repo | ✅ Done (merged to `main`) |
| 2 | Build & publish images to GHCR | ✅ First run green; second run after most recent push should also go green |
| 3 | Provision Hostinger VPS + bring up CPU stack | ✅ Done |
| 4 | DNS + TLS via Cloudflare + Caddy | ✅ Done |
| 5 | RunPod GPU pod setup | ✅ Done (Pod ID: `an5cwp48emcmjj` allocated) |
| 6 | Boot controller integration test | ⏳ Ready (Requires starting a session to test) |
| 7 | Frontend on Cloudflare Pages | ✅ Done |
| 8 | Cameras + smoke test | ⏳ Blocked on you providing RTSP URLs |

## What's in the repo so far (`main`)

| SHA | Subject |
|---|---|
| `9784a23` | chore: add CLAUDE.md and project .gitignore |
| `66397b9` | build: production compose files, GHCR build pipeline, baked GPU images |
| `579c2d0` | feat(api): GPU pod boot controller + async session_start |
| `38f148a` | feat(deploy): cross-domain auth + frontend env wiring for Cloudflare Pages |
| `25fafdd` | build: Caddy reverse proxy for the Hostinger CPU stack |
| `8100601` | docs: deployment runbook + Cloudflare Pages headers |
| `4777f03` | fix(ci): free disk space on github runners before docker builds |
| `0300757` | build(deploy): switch production defaults to the-airco.net |
| `d300767` | build(deploy): switch CPU host provider from Hetzner to Hostinger |
| (this commit) | build(deploy): update GHCR namespace to the-airco-v2 org + DEPLOYMENT.md |

## Key decisions (and why)

| Decision | Why |
|---|---|
| **Hostinger KVM 2 Singapore** for CPU box | $9–12/mo vs $25–30 for Hetzner CCX13 SG (Singapore region carries 40–67% premium on Hetzner + April 2026 price hike). 3–4 user workload doesn't justify dedicated CPU. |
| **RunPod (no Vast.ai fallback for V1)** for GPU | Per-hour billing, ~$0.20/hr for RTX 3090 Community. User opted out of Vast.ai to keep V1 simple. |
| **GHCR private** (not public) | Per-user decision. Adds one auth step at RunPod and one `docker login` on Hostinger. Read-only PAT stored in `creds.txt`. |
| **Tailscale free tier** for CPU↔GPU networking | Avoids exposing Postgres/Redis/MinIO publicly. 3 users / 100 devices is way over our needs. |
| **Direct model bake into images** (no entrypoint sync) | Simpler. RunPod's persistent volume preserves the container filesystem across stop/start, so TRT engines persist anyway. Only a pod *terminate* forces a rebuild. |
| **Triton runs with `--exit-on-error=false`** | Pre-baked `.plan` files are GPU-arch-specific; if they don't match RunPod's RTX 3090 cards, Triton rebuilds from ONNX. First boot adds ~1–2 min; subsequent stop/start are fast. |
| **One image per service** (not a bundled super-image) | Better cache reuse, easier to update one service without rebuilding Triton/Savant. |
| **Async-boot `start_session`** (returns 202) | GPU resume takes 30–60s. UI flips to a "starting" state, polls; background task does the actual pod boot. When `RUNPOD_API_KEY` isn't set (dev) behaviour is unchanged (200, synchronous). |
| **`SESSION_SAME_SITE=none` + `SESSION_COOKIE_DOMAIN=.the-airco.net`** | Frontend on Cloudflare Pages (`app.the-airco.net`) and API on Hostinger (`api.the-airco.net`) are cross-origin; the cookie must be parent-domain-scoped. |
| **CI disk-cleanup step on every build job** | GitHub-hosted runners ship with ~14 GB free; Savant + Triton base images are too big. Pruning preinstalled .NET / Android / CodeQL frees ~25 GB. |

## creds.txt schema (gitignored)

Already in `creds.txt`:
```
# GHCR
GHCR_USERNAME=dscyrus07-dev
GHCR_PAT=ghp_xxx
```

Add tomorrow as you acquire them:
```
# Hostinger
HOSTINGER_VPS_IP=<public IPv4 after VPS creation>
HOSTINGER_VPS_HOSTNAME=airco-hub
HOSTINGER_ROOT_PASSWORD=<whatever you set during setup>

# Supabase (production project)
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# Tailscale
TAILSCALE_AUTHKEY=tskey-auth-...

# Cloudflare
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ZONE_ID=...   # of the-airco.net zone, found at the bottom-right of the zone dashboard

# RunPod (Phase 5 — not needed yet)
RUNPOD_API_KEY=...
RUNPOD_POD_ID=<filled after pod created>
```

## SSH key for the VPS

Generated locally as `~/.ssh/airco_deploy` / `~/.ssh/airco_deploy.pub`.

Public key (paste into Hostinger during VPS setup):
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILtVDT772Ne2yP9WxdZ/9nWbeBDQl/w0oEkAa78UVhSd claude@airco-deploy

Plan changes this is what I generated
Generating public/private ed25519 key pair.
Enter file in which to save the key (/home/nikhil/.ssh/id_ed25519):                                                                               
/home/nikhil/.ssh/id_ed25519 already exists.
Overwrite (y/n)? y
Enter passphrase (empty for no passphrase): 
Enter same passphrase again: 
Your identification has been saved in /home/nikhil/.ssh/id_ed25519
Your public key has been saved in /home/nikhil/.ssh/id_ed25519.pub
The key fingerprint is:
SHA256:mHEZq7L3PtkBPXi3qfRiac4CxkF7bBy1727S1YIT3Wk info@the-airco.com
The key's randomart image is:

```

## Tomorrow — your checklist (in order)

When you sit back down, do these in sequence. Once 1–5 are done, ping me with "go" and I'll start Phase 3.

### 1. Verify GHCR images at the new org namespace

The repo moved to `The-Airco-v2` org, so future image pushes go to `ghcr.io/the-airco-v2/airco-*` (not `ghcr.io/dscyrus07-dev/...`).

- Visit: https://github.com/orgs/The-Airco-v2/packages
- Confirm the 10 `airco-*` packages exist at this URL after the most recent workflow run completes.
- (Optional) Delete the old packages at https://github.com/dscyrus07-dev?tab=packages to avoid confusion.

### 2. Buy Hostinger KVM 2 Singapore

- Plan: **KVM 2** (2 vCPU / 8 GB / 100 GB NVMe / 8 TB BW)
- Location: **Singapore**
- OS: **Ubuntu 24.04 LTS** (or 22.04 LTS)
- Hostname: `airco-hub`
- Root password: set anything (we disable password auth post-setup)
- **SSH key**: paste the ed25519 public key above
- Skip all add-ons

Save the **public IP** Hostinger gives you → add to `creds.txt` as `HOSTINGER_VPS_IP=...`.

### 3. Create production Supabase project (if you don't have one)

- https://supabase.com/dashboard → New project (free tier is fine)
- Settings → API → copy URL, anon key, service_role key
- Add the three values to `creds.txt`

### 4. Sign up for Tailscale + create reusable auth key

- https://login.tailscale.com — Google login OK
- Settings → Keys → **Generate auth key**
  - **Reusable**: ON
  - **Ephemeral**: OFF
  - **Tags**: `tag:airco`
  - Expiration: 90 days
- Add to `creds.txt` as `TAILSCALE_AUTHKEY=tskey-auth-...`

### 5. Generate Cloudflare API token

- https://dash.cloudflare.com/profile/api-tokens → **Create Token** → **Custom token**
- Permissions:
  - **Zone : DNS : Edit** (for the-airco.net)
  - **Account : Cloudflare Pages : Edit** (will be needed at Phase 7; can also add later)
- Zone Resources: Include → Specific zone → `the-airco.net`
- Add to `creds.txt` as `CLOUDFLARE_API_TOKEN=...` and `CLOUDFLARE_ZONE_ID=...` (Zone ID from the zone overview page).

### 6. Ping me with "go"

I'll:
- Update local remote, pull latest from new org
- SSH into your Hostinger VPS using `~/.ssh/airco_deploy`
- Install Docker, Caddy, Tailscale, ufw
- Clone repo, create `.env.production`
- Bring up the CPU compose stack
- Run Alembic migrations
- Confirm `/health` reachable via the public IP

That's roughly 60–90 minutes of my work for Phase 3, end-to-end, plus another 15–30 min for Phase 4 (DNS + TLS) right after.

## What's still ahead after tomorrow

- **Phase 5**: RunPod billing + API key + click-through to add GHCR registry credential in their UI. Then I create the pod template programmatically.
- **Phase 6**: End-to-end test of `POST /sessions/:id/start` → pod boot → `session_start` → analytics flow.
- **Phase 7**: Cloudflare Pages setup (you click "Connect to Git" once in the browser; I configure build settings via API).
- **Phase 8**: Add cameras + first real session.

## Notes & gotchas to remember

* **Branch Merged:** The `deploy/ghcr-and-compose` code has been successfully merged into `main`, and production now tracks `main`.
* **GHA Tags Simplified:** The `.github/workflows/build-images.yml` workflow has been cleaned up to only tag images as `:latest` when building from `main`.
* **Pycache Cleanup:** Tracked `.pyc` byte-code files under `services/savant-pipeline/__pycache__/` have been successfully untracked (`git rm --cached`).
* **Test Suite Green:** Pre-existing test failures (such as the missing `SESSION_SECRET` in `test_deploy_env.py` and local MinIO url hostname normalizations) have been fixed and all 347 tests are green.

