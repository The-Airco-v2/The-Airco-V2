---
name: runpod-lifecycle
description: Manage GPU pods on RunPod — allocate, stop, resume, terminate, query status, debug bootstrap, and troubleshoot Tailscale/Podman setup on community cloud pods.
---

# RunPod GPU Pod Lifecycle Skill

Use this skill to manage the on-demand GPU pod that powers the Airco analytics pipeline.

## Key Facts

| Item | Value |
|---|---|
| **API Key** | `<RUNPOD_API_KEY>` |
| **API Endpoint** | `https://api.runpod.io/graphql` |
| **Current Pod** | See `RUNPOD_POD_ID` in `/app/airco/.env.production` on VPS |
| **Base Image** | `ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest` |
| **Persistent Volume** | `/workspace` (40 GB) |
| **Bootstrap Log** | `/workspace/bootstrap.log` on the pod |

> [!IMPORTANT]
> RunPod's API is behind Cloudflare WAF. Always set `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36` or requests will get 403 blocked.

## Current bootstrap model

The current pod does **not** use raw `dockerArgs` bootstrap anymore. It uses the
private image `ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest`, and pod
creation must include `containerRegistryAuthId` so RunPod can pull that image.

The image ENTRYPOINT is `Backend/docker/bootstrap-entrypoint.sh` and it:
- starts `tailscaled` with `--tun=userspace-networking`
- joins Tailscale as `airco-gpu`
- clones / pulls `/workspace/The-Airco-V2`
- writes `Backend/.env.production`
- runs the GPU stack with Podman

Use the old `dockerArgs` guidance only when reading historical notes.

## Why Podman Instead of Docker

RunPod **community** pods do NOT have `CAP_NET_ADMIN` or privileged access.
`dockerd` needs these to set up bridge networks and iptables — it will fail
with `iptables: Permission denied`. **Use Podman instead:**
- Podman uses `slirp4netns` for networking (no kernel net capabilities needed)
- Podman uses CDI (Container Device Interface) for GPU access
- `podman-compose` reads the same `docker-compose.gpu.yml` format

## Diagnosing Pod Problems

### Check if pod is alive (uptimeSeconds > 0 means container is running)
```python
import urllib.request, json
API_KEY = "<RUNPOD_API_KEY>"
POD_ID = "<pod-id>"
query = """query Pod($podId: String!) {
    pod(input: { podId: $podId }) {
        id desiredStatus uptimeSeconds machineId
        runtime { gpus { id } ports { ip isIpPublic privatePort publicPort type } }
    }
}"""
req = urllib.request.Request("https://api.runpod.io/graphql", method="POST",
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json",
             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
req.data = json.dumps({"query": query, "variables": {"podId": POD_ID}}).encode()
with urllib.request.urlopen(req) as r:
    print(json.dumps(json.loads(r.read()), indent=2))
```

### `uptimeSeconds: 0` — container crashed immediately
- On the current bootstrap image, this usually means the image pull failed, the registry auth was missing, or the entrypoint exited very early.
- Check if `machineId` is the same as previous failed pods — if so, that RunPod host is broken. Terminate and redeploy targeting a different GPU type.
- Previously broken host: `ttulpyxydm59` (RTX 3090 community pool).

### Check Tailscale connectivity (primary success indicator)
```bash
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale status'
# Should show airco-gpu when pod bootstrap has reached the tailscale up step
```

### SSH into pod via VPS Tailscale hop
```bash
# Get GPU pod Tailscale IP from tailscale status output, then:
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 \
  "ssh -o StrictHostKeyChecking=no root@<airco-gpu-tailscale-ip>"
# On pod:
cat /workspace/bootstrap.log   # full bootstrap log
podman ps                       # running containers
nvidia-smi                      # GPU status
```

## GraphQL Operations

### List all pods
```bash
curl -s -X POST https://api.runpod.io/graphql \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  -d '{"query": "query { myself { pods { id name desiredStatus imageName } } }"}'
```

### Resume (start) a stopped pod
```bash
curl -s -X POST https://api.runpod.io/graphql \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  -d '{"query": "mutation Resume($input: PodResumeInput!) { podResume(input: $input) { id desiredStatus } }", "variables": {"input": {"podId": "<POD_ID>", "gpuCount": 1}}}'
```

### Stop pod (park — keeps volume, stops billing)
```bash
curl -s -X POST https://api.runpod.io/graphql \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  -d '{"query": "mutation Stop($input: PodStopInput!) { podStop(input: $input) { id desiredStatus } }", "variables": {"input": {"podId": "<POD_ID>"}}}'
```

### Terminate pod (delete — destroys volume!)
```bash
curl -s -X POST https://api.runpod.io/graphql \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  -d '{"query": "mutation Terminate($input: PodTerminateInput!) { podTerminate(input: $input) }", "variables": {"input": {"podId": "<POD_ID>"}}}'
```

## Creating a New Pod

Preferred paths:

1. Local:
   `python3 Backend/scripts/create_runpod_pod.py --env-file Backend/.env.runpod`
2. GitHub Actions:
   `.github/workflows/deploy-runpod-pod.yml`

Required inputs:
- `Backend/.env.runpod` or `RUNPOD_ENV_FILE` secret
- `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` for the private GHCR bootstrap image
- `AUTO_LINK_HOSTINGER=1` for automatic Hostinger cutover

The bootstrap repo path on the pod is `/workspace/The-Airco-V2`.

GPU image refresh behavior:
- normal `push` to `main` rebuilds only GPU images whose paths changed
- manual `workflow_dispatch` with `full_rebuild=false` keeps unchanged GPU images as-is
- manual `workflow_dispatch` with `full_rebuild=true` forces rebuilding all GPU images

GPU type preference order (try in sequence, some may be unavailable):
1. `NVIDIA RTX A6000` → machine `hu32823wq6hy` (known working)
2. `NVIDIA RTX A5000`
3. `NVIDIA GeForce RTX 3080 Ti`
4. `NVIDIA RTX A4000`
5. `NVIDIA GeForce RTX 3090` (avoid — prone to landing on broken host `ttulpyxydm59`)

When `AUTO_LINK_HOSTINGER=1` is set, `create_runpod_pod.py` updates
`RUNPOD_POD_ID` in `/app/airco/.env.production` and restarts `airco-api`
automatically over SSH.

## GPU Idle Loop (Backend Controller)

`services/api/api/gpu_controller.py` + `runpod_client.py` manage lifecycle:
- **Start session** → `ensure_running()` → `podResume` → polls `GPU_HEALTH_TARGET` URL
- **Idle timeout** → `release_if_idle()` → `podStop` (configurable via `GPU_IDLE_TIMEOUT_SECONDS`)
- **No-op** when `RUNPOD_API_KEY` / `RUNPOD_POD_ID` are unset (safe for local dev)

Key env vars on VPS:
- `GPU_IDLE_TIMEOUT_SECONDS=900` — auto-stop after 15min of no active sessions
- `GPU_BOOT_TIMEOUT_SECONDS=180` — max wait for health check
- `GPU_HEALTH_TARGET=` — HTTP URL to poll (e.g. `http://airco-gpu:8002/v2/health/ready`)
