#!/usr/bin/env python3
"""
create_runpod_pod.py — Deploy the Airco GPU stack on RunPod Community Cloud.

Usage:
    python3 Backend/scripts/create_runpod_pod.py

After running, copy the printed RUNPOD_POD_ID into /app/airco/.env.production
on the Hostinger VPS, then restart the API:
    ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'

Key design decisions:
- Uses ubuntu:22.04 as base (no locked entrypoint)
- dockerArgs must NOT use 'bash -c "..."' with inner double quotes — RunPod's
  GraphQL API JSON-encodes them and the shell receives mangled input.
  Instead we write a script to /tmp via python3 -c and exec it.
- Uses Podman instead of Docker (no CAP_NET_ADMIN / iptables needed on community pods)
- Bootstrap script lives in Backend/scripts/gpu_bootstrap.sh (pulled from git)
"""

import json
import os
import sys
import urllib.request

# ── Secrets (read from env if available, fall back to defaults for local use) ──
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "rpa_REPLACE_ME")
GHCR_PAT = os.environ.get("GHCR_PAT", "ghp_REPLACE_ME")
TS_AUTHKEY = os.environ.get("TAILSCALE_AUTHKEY", "tskey-auth-REPLACE_ME")
GIT_REPO = "https://nick2580:${GHCR_PAT}@github.com/The-Airco-v2/The-Airco-V2.git"

RUNPOD_API_URL = "https://api.runpod.io/graphql"

# ── Bootstrap: write script via python3 then exec — avoids all quote escaping ──
# python3 -c writes the script file character-by-character so no shell quoting
# is needed in the JSON. The script then runs directly.
BOOTSTRAP_SCRIPT = """#!/bin/bash
set -euo pipefail
exec &>/workspace/bootstrap.log

echo "=== Airco GPU Bootstrap $(date -u) ==="

# 1. Tailscale
apt-get update -qq
apt-get install -y curl git
curl -fsSL https://tailscale.com/install.sh | sh
tailscaled --state=/workspace/tailscale.state >/tmp/tailscaled.log 2>&1 &
sleep 8
tailscale up --authkey={ts_authkey} --accept-routes=false --hostname=airco-gpu
echo "TAILSCALE_OK"

# 2. Clone / update repo
REPO=/workspace/The-Airco-V2
if [ -d "$REPO/.git" ]; then
    git -C "$REPO" pull
else
    git clone https://nick2580:{ghcr_pat}@github.com/The-Airco-v2/The-Airco-V2.git "$REPO"
fi

# 3. Run main bootstrap (Podman + NVIDIA + compose)
bash "$REPO/Backend/scripts/gpu_bootstrap.sh"
""".format(ts_authkey=TS_AUTHKEY, ghcr_pat=GHCR_PAT)

# Encode the script as a python3 -c that writes it to a file and runs it.
# This avoids any shell quoting issues in dockerArgs JSON.
ESCAPED = BOOTSTRAP_SCRIPT.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
DOCKER_ARGS = (
    f"python3 -c \""
    f"import os; "
    f"open('/tmp/boot.sh','w').write('{ESCAPED}'); "
    f"os.chmod('/tmp/boot.sh', 0o755); "
    f"os.execv('/bin/bash', ['/bin/bash', '/tmp/boot.sh'])"
    f"\""
)

CREATE_MUTATION = """
mutation CreatePod($input: PodFindAndDeployOnDemandInput!) {
    podFindAndDeployOnDemand(input: $input) {
        id
        desiredStatus
        imageName
        machineId
    }
}
"""

POD_CONFIG = {
    "cloudType": "COMMUNITY",
    "gpuCount": 1,
    "volumeInGb": 40,
    "containerDiskInGb": 40,
    "minVcpuCount": 2,
    "minMemoryInGb": 15,
    "name": "airco-gpu-stack",
    "imageName": "ubuntu:22.04",
    "dockerArgs": DOCKER_ARGS,
    "ports": "22/tcp,80/http",
    "volumeMountPath": "/workspace",
    "env": [
        {"key": "POSTGRES_PASSWORD",       "value": os.environ.get("POSTGRES_PASSWORD", "")},
        {"key": "MINIO_ACCESS_KEY",         "value": os.environ.get("MINIO_ACCESS_KEY", "")},
        {"key": "MINIO_SECRET_KEY",         "value": os.environ.get("MINIO_SECRET_KEY", "")},
        {"key": "CENTRIFUGO_API_KEY",       "value": os.environ.get("CENTRIFUGO_API_KEY", "")},
        {"key": "CENTRIFUGO_TOKEN_SECRET",  "value": os.environ.get("CENTRIFUGO_TOKEN_SECRET", "")},
        {"key": "SESSION_SECRET",           "value": os.environ.get("SESSION_SECRET", "")},
        {"key": "SUPABASE_URL",             "value": os.environ.get("SUPABASE_URL", "")},
        {"key": "SUPABASE_ANON_KEY",        "value": os.environ.get("SUPABASE_ANON_KEY", "")},
        {"key": "SUPABASE_SERVICE_ROLE_KEY","value": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")},
        {"key": "AIRCO_HUB_HOST",           "value": os.environ.get("AIRCO_HUB_HOST", "100.103.80.105")},
        {"key": "PUBLIC_MINIO_URL",         "value": os.environ.get("PUBLIC_MINIO_URL", "")},
        {"key": "GHCR_PAT",                 "value": GHCR_PAT},
    ]
}

# GPU types to try, in preference order.
GPU_TYPES = [
    "NVIDIA RTX A6000",
    "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 3080 Ti",
    "NVIDIA RTX A4000",
    "NVIDIA GeForce RTX 3090",
    "NVIDIA A40",
    "NVIDIA L40",
]

HEADERS = {
    "Authorization": f"Bearer {RUNPOD_API_KEY}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def graphql(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(RUNPOD_API_URL, method="POST", headers=HEADERS, data=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    if RUNPOD_API_KEY == "rpa_REPLACE_ME":
        print("ERROR: Set RUNPOD_API_KEY env var or edit this script.", file=sys.stderr)
        sys.exit(1)

    for gpu_type in GPU_TYPES:
        print(f"Trying {gpu_type} ...", end=" ", flush=True)
        config = {**POD_CONFIG, "gpuTypeId": gpu_type}
        try:
            res = graphql(CREATE_MUTATION, {"input": config})
        except Exception as e:
            print(f"request error: {e}")
            continue

        errors = res.get("errors")
        pod = (res.get("data") or {}).get("podFindAndDeployOnDemand")
        if pod:
            print(f"SUCCESS")
            print(f"\n  Pod ID:     {pod['id']}")
            print(f"  Machine ID: {pod.get('machineId', 'unknown')}")
            print(f"  Image:      {pod['imageName']}")
            print(f"\nNext steps:")
            print(f"  1. Update RUNPOD_POD_ID on VPS:")
            print(f"     ssh root@72.61.239.69 'sed -i \"s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID={pod['id']}/\" /app/airco/.env.production'")
            print(f"  2. Restart API:")
            print(f"     ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'")
            print(f"  3. Watch bootstrap log (once Tailscale connects):")
            print(f"     ssh root@72.61.239.69 'tailscale status'  # wait for airco-gpu")
            return
        else:
            msg = errors[0]["message"] if errors else "unknown error"
            print(f"failed: {msg}")

    print("\nCould not deploy on any GPU type. Check RunPod dashboard for availability.")
    sys.exit(1)


if __name__ == "__main__":
    main()
