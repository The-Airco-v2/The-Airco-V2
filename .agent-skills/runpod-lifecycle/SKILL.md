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
| **Base Image** | `ubuntu:22.04` |
| **Persistent Volume** | `/workspace` (40 GB) |
| **Bootstrap Log** | `/workspace/bootstrap.log` on the pod |

> [!IMPORTANT]
> RunPod's API is behind Cloudflare WAF. Always set `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36` or requests will get 403 blocked.

## Critical: `dockerArgs` Shell Wrapping Rule

`ubuntu:22.04` has no ENTRYPOINT and `CMD=["/bin/bash"]`. RunPod uses `dockerArgs`
as a CMD override. **You MUST wrap commands in `bash -c "..."` for shell operators
(`&&`, `|`, `;`) to work.** Without this wrapper, the pod crashes immediately with
`uptimeSeconds: 0`.

**Working pattern:**
```
bash -c "exec &>/workspace/bootstrap.log; set -x; apt-get install ... && ..."
```

**Never do:** `curl ... | sh && tailscale up ...` (no bash -c wrapper — shell operators won't work)

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
- Almost always means `dockerArgs` has no `bash -c "..."` wrapper, OR the machine has issues.
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

Use `Backend/scripts/gpu_bootstrap.sh` (in the git repo). The `dockerArgs` should be:

```python
dockerArgs = (
    'bash -c "'
    'exec &>/workspace/bootstrap.log; set -x; '
    'apt-get update -qq && apt-get install -y curl git && '
    'curl -fsSL https://tailscale.com/install.sh | sh && '
    'tailscaled --state=/workspace/tailscale.state >/tmp/tailscaled.log 2>&1 & '
    'sleep 5 && '
    'tailscale up --authkey=<TS_KEY> --accept-routes=false --hostname=airco-gpu && '
    'if [ -d /workspace/The-Airco-V2/.git ]; then git -C /workspace/The-Airco-V2 pull; '
    'else git clone https://nick2580:<GHCR_PAT>@github.com/The-Airco-v2/The-Airco-V2.git /workspace/The-Airco-V2; fi && '
    'bash /workspace/The-Airco-V2/Backend/scripts/gpu_bootstrap.sh'
    '"'
)
```

The bootstrap repo path on the pod is `/workspace/The-Airco-V2`. Unlike the
Hostinger CPU deploy, the GPU pod bootstrap **does** use a git checkout there
because it reclones or pulls the repo on pod startup before running
`Backend/scripts/gpu_bootstrap.sh`.

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

After creating a pod, update `RUNPOD_POD_ID` in `/app/airco/.env.production` on the VPS:
```bash
ssh root@72.61.239.69 'sed -i "s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID=<NEW_ID>/" /app/airco/.env.production'
ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'
```

## GPU Idle Loop (Backend Controller)

`services/api/api/gpu_controller.py` + `runpod_client.py` manage lifecycle:
- **Start session** → `ensure_running()` → `podResume` → polls `GPU_HEALTH_TARGET` URL
- **Idle timeout** → `release_if_idle()` → `podStop` (configurable via `GPU_IDLE_TIMEOUT_SECONDS`)
- **No-op** when `RUNPOD_API_KEY` / `RUNPOD_POD_ID` are unset (safe for local dev)

Key env vars on VPS:
- `GPU_IDLE_TIMEOUT_SECONDS=900` — auto-stop after 15min of no active sessions
- `GPU_BOOT_TIMEOUT_SECONDS=180` — max wait for health check
- `GPU_HEALTH_TARGET=` — HTTP URL to poll (e.g. `http://airco-gpu:8002/v2/health/ready`)
