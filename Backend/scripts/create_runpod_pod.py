#!/usr/bin/env python3
"""
create_runpod_pod.py — Deploy the Airco GPU stack on RunPod Community Cloud.

Usage (from the Hostinger VPS or locally with env vars set):
    export RUNPOD_API_KEY=rpa_...
    export TAILSCALE_AUTHKEY=tskey-auth-...
    export GHCR_PAT=ghp_...
    # + all POSTGRES_PASSWORD, MINIO_*, CENTRIFUGO_*, etc.
    python3 Backend/scripts/create_runpod_pod.py

After running, update RUNPOD_POD_ID on the VPS and restart the API:
    ssh root@72.61.239.69 'sed -i "s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID=<ID>/" /app/airco/.env.production'
    ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'

Design:
    Uses ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest as the base image.
    This image has Tailscale, Podman, and NVIDIA CTK pre-installed.
    The ENTRYPOINT script reads all secrets from pod env vars — no dockerArgs
    quoting issues. Build the image with Backend/docker/Dockerfile.runpod-bootstrap.
"""

import json
import os
import sys
import urllib.request

# ── Secrets ────────────────────────────────────────────────────────────────────
RUNPOD_API_KEY  = os.environ.get("RUNPOD_API_KEY", "")
TAILSCALE_AUTHKEY = os.environ.get("TAILSCALE_AUTHKEY", "")
GHCR_PAT        = os.environ.get("GHCR_PAT", "")

RUNPOD_API_URL = "https://api.runpod.io/graphql"
BOOTSTRAP_IMAGE = "ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest"

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

def build_pod_config(gpu_type: str) -> dict:
    return {
        "cloudType": "COMMUNITY",
        "gpuCount": 1,
        "gpuTypeId": gpu_type,
        "volumeInGb": 40,
        "containerDiskInGb": 40,
        "minVcpuCount": 2,
        "minMemoryInGb": 15,
        "name": "airco-gpu-stack",
        "imageName": BOOTSTRAP_IMAGE,
        "dockerArgs": "",  # ENTRYPOINT handles everything
        "ports": "22/tcp,80/http",
        "volumeMountPath": "/workspace",
        "env": [
            {"key": "TAILSCALE_AUTHKEY",       "value": TAILSCALE_AUTHKEY},
            {"key": "GHCR_PAT",                "value": GHCR_PAT},
            {"key": "POSTGRES_PASSWORD",       "value": os.environ.get("POSTGRES_PASSWORD", "")},
            {"key": "MINIO_ACCESS_KEY",        "value": os.environ.get("MINIO_ACCESS_KEY", "")},
            {"key": "MINIO_SECRET_KEY",        "value": os.environ.get("MINIO_SECRET_KEY", "")},
            {"key": "CENTRIFUGO_API_KEY",      "value": os.environ.get("CENTRIFUGO_API_KEY", "")},
            {"key": "CENTRIFUGO_TOKEN_SECRET", "value": os.environ.get("CENTRIFUGO_TOKEN_SECRET", "")},
            {"key": "SESSION_SECRET",          "value": os.environ.get("SESSION_SECRET", "")},
            {"key": "SUPABASE_URL",            "value": os.environ.get("SUPABASE_URL", "")},
            {"key": "SUPABASE_ANON_KEY",       "value": os.environ.get("SUPABASE_ANON_KEY", "")},
            {"key": "SUPABASE_SERVICE_ROLE_KEY","value": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")},
            {"key": "AIRCO_HUB_HOST",          "value": os.environ.get("AIRCO_HUB_HOST", "100.103.80.105")},
            {"key": "PUBLIC_MINIO_URL",        "value": os.environ.get("PUBLIC_MINIO_URL", "")},
        ],
    }


# GPU types to try in preference order.
GPU_TYPES = [
    "NVIDIA RTX A6000",
    "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 3080 Ti",
    "NVIDIA RTX A4000",
    "NVIDIA A40",
    "NVIDIA L40",
    "NVIDIA GeForce RTX 3090",
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
    missing = [k for k in ("RUNPOD_API_KEY", "TAILSCALE_AUTHKEY", "GHCR_PAT") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        print("Set them before running this script.", file=sys.stderr)
        sys.exit(1)

    for gpu_type in GPU_TYPES:
        print(f"Trying {gpu_type} ...", end=" ", flush=True)
        try:
            res = graphql(CREATE_MUTATION, {"input": build_pod_config(gpu_type)})
        except Exception as e:
            print(f"request error: {e}")
            continue

        errors = res.get("errors")
        pod = (res.get("data") or {}).get("podFindAndDeployOnDemand")
        if pod:
            print("SUCCESS")
            print(f"\n  Pod ID:     {pod['id']}")
            print(f"  Machine ID: {pod.get('machineId', 'unknown')}")
            print(f"  Image:      {pod['imageName']}")
            print(f"\nNext steps:")
            print(f"  1. Update RUNPOD_POD_ID on the VPS:")
            print(f"     ssh root@72.61.239.69 'sed -i \"s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID={pod['id']}/\" /app/airco/.env.production'")
            print(f"  2. Restart the API:")
            print(f"     ssh root@72.61.239.69 'cd /app/airco && docker compose -f docker-compose.cpu.yml restart api'")
            print(f"  3. Watch bootstrap (once Tailscale connects):")
            print(f"     ssh root@72.61.239.69 'tailscale status'  # wait for airco-gpu peer")
            return
        else:
            msg = (errors or [{}])[0].get("message", "unknown error")
            print(f"failed: {msg}")

    print("\nCould not deploy on any GPU type. Check RunPod dashboard for availability.")
    sys.exit(1)


if __name__ == "__main__":
    main()
