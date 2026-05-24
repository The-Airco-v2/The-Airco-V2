---
name: tailscale-networking
description: Debug, inspect, and manage the Tailscale overlay network connecting the Hostinger VPS (airco-hub) and RunPod GPU pod (airco-gpu). Covers status checks, peer reachability, IP lookup, and re-authentication procedures.
---

# Tailscale Networking Skill

Use this skill to inspect and manage the Tailscale overlay network that connects
the Hostinger VPS and RunPod GPU pod.

## Network Topology

```
Local machine (optional Tailscale)
        │
        ▼
airco-hub (Hostinger VPS)          airco-gpu (RunPod pod)
  100.103.80.105                     100.x.x.x (changes per deployment)
  Registered as: srv1696728          Registered as: airco-gpu
  Persistent node                    Ephemeral node (same identity via /workspace state)
        │                                    │
        └────────── Tailscale VPN ───────────┘
```

All inter-service traffic between VPS and GPU pod flows over this encrypted
Tailscale mesh — Postgres, Redis, MinIO, Centrifugo, go2rtc.

## Auth Key

- **Tailscale Auth Key:** `tskey-auth-kCi9o6yMF211CNTRL-...`
- Stored in: `TAILSCALE_AUTHKEY` in `/app/airco/.env.production` on VPS
- Used by the bootstrap image entrypoint to connect on startup

> [!WARNING]
> Tailscale auth keys expire. If the GPU pod can't connect (tailscale up fails with
> "auth key expired"), generate a new key at https://login.tailscale.com/admin/settings/keys
> and update both the VPS env file and `Backend/.env.runpod` / `RUNPOD_ENV_FILE` (requires re-creating the pod).

## Check Tailscale Status

### From VPS (via SSH)
```bash
# List connected peers
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale status'
# Expected when GPU pod is running: shows airco-gpu peer with its IP

# Ping the GPU pod
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale ping airco-gpu'

# Get detailed JSON status including IPs and connection states
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale status --json | python3 -m json.tool'

# Check VPS's own IP and hostname on tailnet
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale ip'
```

### From GPU pod (via VPS hop)
```bash
# First get GPU pod IP from VPS tailscale status
GPU_IP=$(ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 'tailscale status --json' \
  | python3 -c "import json,sys; peers=json.load(sys.stdin)['Peer']; \
    [print(v['TailscaleIPs'][0]) for v in peers.values() if v.get('HostName','').startswith('airco-gpu')]")

# SSH into GPU pod via VPS Tailscale hop
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 "ssh -o StrictHostKeyChecking=no root@${GPU_IP}"

# Or one-liner check of GPU pod tailscale
ssh -i ~/.ssh/id_ed25519 root@72.61.239.69 "ssh -o StrictHostKeyChecking=no root@${GPU_IP} 'tailscale status'"
```

## GPU Pod Tailscale Startup

The GPU pod runs Tailscale from `Backend/docker/bootstrap-entrypoint.sh`. State
is persisted to `/workspace/tailscale.state` so the same node identity is reused
across pod stop/start:

```bash
# On the pod (run as part of bootstrap-entrypoint.sh):
TAILSCALE_SOCKET=/tmp/tailscaled.sock
tailscaled --state=/workspace/tailscale.state \
  --socket="${TAILSCALE_SOCKET}" \
  --tun=userspace-networking >/tmp/tailscaled.log 2>&1 &
sleep 8
tailscale --socket="${TAILSCALE_SOCKET}" up --authkey=tskey-auth-kCi9o6yMF211CNTRL-... \
  --accept-routes=false --hostname=airco-gpu

# Check if connected (on pod):
tailscale --socket="${TAILSCALE_SOCKET}" status
tailscale --socket="${TAILSCALE_SOCKET}" ip
```

The usual startup order is:
1. Hostinger API resumes the RunPod pod
2. Pod bootstrap starts `tailscaled` in userspace networking mode
3. Pod joins the tailnet as `airco-gpu`
4. Pod bootstrap pulls `/workspace/The-Airco-V2` and starts the GPU compose stack

## Debugging Tailscale Issues

### GPU pod not showing in `tailscale status` on VPS

1. **Check if pod bootstrap is even running** — look at `uptimeSeconds` in RunPod API query.
   If 0, the container crashed before reaching the tailscale step.

2. **Check tailscaled log on pod:**
   ```bash
   # SSH into pod via its RunPod SSH port (from RunPod console or API port query)
   cat /tmp/tailscaled.log
   ```

3. **Auth key expired** — If tailscaled log shows auth error, regenerate the key.

4. **Stale node identity** — If `/workspace/tailscale.state` has a node that's been
   removed from the Tailscale admin console, `tailscale up` will fail. Fix:
   ```bash
   rm /workspace/tailscale.state
   tailscale up --authkey=<new-key> --hostname=airco-gpu
   ```

5. **Daemon exited early** — if bootstrap log shows `failed to connect to local tailscaled`,
   inspect `/tmp/tailscaled.log`. On community pods the daemon must run with
   `--tun=userspace-networking`; kernel-TUN mode is not reliable there.

### VPS can't reach GPU pod services

```bash
# Test TCP reachability from VPS to GPU pod
ssh root@72.61.239.69 "nc -zv <GPU_TAILSCALE_IP> 8001"  # Triton gRPC
ssh root@72.61.239.69 "nc -zv <GPU_TAILSCALE_IP> 8002"  # Triton HTTP

# Check if Podman/service is actually listening on GPU pod
ssh root@72.61.239.69 "ssh root@<GPU_IP> 'podman ps && ss -tlnp'"
```

## VPS Tailscale Daemon Management

The VPS tailscale is managed by the system package:
```bash
# Check tailscale daemon status on VPS
ssh root@72.61.239.69 'systemctl status tailscaled'

# Restart if needed
ssh root@72.61.239.69 'systemctl restart tailscaled'

# Re-authenticate VPS (if key expired)
ssh root@72.61.239.69 'tailscale up --authkey=<new-key> --hostname=airco-hub'
```

## IP Address Reference

| Host | Public IP | Tailscale IP | Hostname |
|---|---|---|---|
| Hostinger VPS | `72.61.239.69` | `100.103.80.105` | `airco-hub` |
| RunPod GPU pod | dynamic (RunPod) | dynamic (check `tailscale status`) | `airco-gpu` |

The GPU pod's Tailscale IP changes between termination/recreation but the hostname
`airco-gpu` stays constant while the pod is alive. Use `AIRCO_HUB_HOST=100.103.80.105`
(static VPS Tailscale IP) for GPU services to find the VPS.
