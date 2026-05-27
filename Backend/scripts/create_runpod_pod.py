#!/usr/bin/env python3
"""
create_runpod_pod.py — Deploy the Airco GPU stack on RunPod Community Cloud.

Usage (from the Hostinger VPS or locally with env vars set):
    python3 Backend/scripts/create_runpod_pod.py --env-file Backend/.env.runpod

The env file can define:
    RUNPOD_API_KEY=rpa_...
    TAILSCALE_AUTHKEY=tskey-auth-...
    GHCR_PAT=ghp_...
    RUNPOD_CONTAINER_REGISTRY_AUTH_ID=clzdaifot0001l90809257ynb
    AUTO_LINK_HOSTINGER=1
    # + POSTGRES_PASSWORD, MINIO_*, CENTRIFUGO_*, SUPABASE_*, etc.

When AUTO_LINK_HOSTINGER=1 is set, the script also updates RUNPOD_POD_ID on
Hostinger and restarts the API automatically.

Design:
    Uses ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest as the base image.
    This image has Tailscale, Podman, and NVIDIA CTK pre-installed.
    The ENTRYPOINT script reads all secrets from pod env vars — no dockerArgs
    quoting issues. Build the image with Backend/docker/Dockerfile.runpod-bootstrap.
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

RUNPOD_API_URL = "https://api.runpod.io/graphql"
BOOTSTRAP_IMAGE = "ghcr.io/the-airco-v2/airco-runpod-bootstrap:latest"
ENV_FILE_VALUES: dict[str, str] = {}
RUNPOD_CONTAINER_REGISTRY_AUTH_ID = os.environ.get("RUNPOD_CONTAINER_REGISTRY_AUTH_ID", "")
HOSTINGER_SSH_TARGET = os.environ.get("HOSTINGER_SSH_TARGET", "root@72.61.239.69")
HOSTINGER_ENV_FILE = os.environ.get("HOSTINGER_ENV_FILE", "/app/airco/.env.production")
HOSTINGER_APP_DIR = os.environ.get("HOSTINGER_APP_DIR", "/app/airco")

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


def load_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve_env(key: str, env_file_values: dict[str, str], default: str = "") -> str:
    return os.environ.get(key) or env_file_values.get(key, default)


def env_flag(key: str, env_file_values: dict[str, str]) -> bool:
    return resolve_env(key, env_file_values).lower() in {"1", "true", "yes"}


def parse_args(argv: list[str]) -> dict[str, str | None]:
    env_file = None
    idx = 0
    while idx < len(argv):
        if argv[idx] == "--env-file":
            idx += 1
            if idx >= len(argv):
                raise SystemExit("--env-file requires a path")
            env_file = argv[idx]
        else:
            raise SystemExit(f"unknown argument: {argv[idx]}")
        idx += 1
    return {"env_file": env_file}


def build_pod_config(gpu_type: str, env_file_values: dict[str, str] | None = None) -> dict:
    env_file_values = env_file_values or ENV_FILE_VALUES
    
    # Read local SSH public key to authorize on the pod
    ssh_public_key = ""
    for key_name in ("id_ed25519.pub", "id_rsa.pub"):
        pub_path = Path.home() / ".ssh" / key_name
        if pub_path.exists():
            ssh_public_key = pub_path.read_text().strip()
            break

    config = {
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
            {"key": "SSH_PUBLIC_KEY",          "value": ssh_public_key},
            {"key": "TAILSCALE_AUTHKEY",       "value": resolve_env("TAILSCALE_AUTHKEY", env_file_values)},
            {"key": "GHCR_PAT",                "value": resolve_env("GHCR_PAT", env_file_values)},
            {"key": "POSTGRES_PASSWORD",       "value": resolve_env("POSTGRES_PASSWORD", env_file_values)},
            {"key": "MINIO_ACCESS_KEY",        "value": resolve_env("MINIO_ACCESS_KEY", env_file_values)},
            {"key": "MINIO_SECRET_KEY",        "value": resolve_env("MINIO_SECRET_KEY", env_file_values)},
            {"key": "CENTRIFUGO_API_KEY",      "value": resolve_env("CENTRIFUGO_API_KEY", env_file_values)},
            {"key": "CENTRIFUGO_TOKEN_SECRET", "value": resolve_env("CENTRIFUGO_TOKEN_SECRET", env_file_values)},
            {"key": "SESSION_SECRET",          "value": resolve_env("SESSION_SECRET", env_file_values)},
            {"key": "SUPABASE_URL",            "value": resolve_env("SUPABASE_URL", env_file_values)},
            {"key": "SUPABASE_ANON_KEY",       "value": resolve_env("SUPABASE_ANON_KEY", env_file_values)},
            {"key": "SUPABASE_SERVICE_ROLE_KEY","value": resolve_env("SUPABASE_SERVICE_ROLE_KEY", env_file_values)},
            {"key": "AIRCO_HUB_HOST",          "value": resolve_env("AIRCO_HUB_HOST", env_file_values, "100.103.80.105")},
            {"key": "PUBLIC_MINIO_URL",        "value": resolve_env("PUBLIC_MINIO_URL", env_file_values)},
        ],
    }
    registry_auth_id = resolve_env(
        "RUNPOD_CONTAINER_REGISTRY_AUTH_ID",
        env_file_values,
        RUNPOD_CONTAINER_REGISTRY_AUTH_ID,
    )
    if registry_auth_id:
        config["containerRegistryAuthId"] = registry_auth_id
    return config


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

def graphql(query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {resolve_env('RUNPOD_API_KEY', ENV_FILE_VALUES)}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(RUNPOD_API_URL, method="POST", headers=headers, data=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def link_hostinger_to_pod(pod_id: str, env_file_values: dict[str, str] | None = None) -> None:
    env_file_values = env_file_values or ENV_FILE_VALUES
    hostinger_ssh_target = resolve_env("HOSTINGER_SSH_TARGET", env_file_values, HOSTINGER_SSH_TARGET)
    hostinger_env_file = resolve_env("HOSTINGER_ENV_FILE", env_file_values, HOSTINGER_ENV_FILE)
    hostinger_app_dir = resolve_env("HOSTINGER_APP_DIR", env_file_values, HOSTINGER_APP_DIR)
    update_cmd = (
        f"sed -i 's/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID={pod_id}/' {hostinger_env_file}"
    )
    restart_cmd = (
        f"cd {hostinger_app_dir} && "
        f"docker compose -f docker-compose.cpu.yml --env-file .env.production restart api"
    )

    subprocess.run(["ssh", hostinger_ssh_target, update_cmd], check=True)
    subprocess.run(["ssh", hostinger_ssh_target, restart_cmd], check=True)


def main() -> None:
    global ENV_FILE_VALUES
    args = parse_args(sys.argv[1:])
    if args["env_file"]:
        ENV_FILE_VALUES = load_env_file(args["env_file"])

    missing = [
        key for key in ("RUNPOD_API_KEY", "TAILSCALE_AUTHKEY", "GHCR_PAT")
        if not resolve_env(key, ENV_FILE_VALUES)
    ]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        print("Set them in the shell or pass --env-file.", file=sys.stderr)
        sys.exit(1)

    for gpu_type in GPU_TYPES:
        print(f"Trying {gpu_type} ...", end=" ", flush=True)
        try:
            res = graphql(CREATE_MUTATION, {"input": build_pod_config(gpu_type, ENV_FILE_VALUES)})
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
            hostinger_ssh_target = resolve_env("HOSTINGER_SSH_TARGET", ENV_FILE_VALUES, HOSTINGER_SSH_TARGET)
            hostinger_env_file = resolve_env("HOSTINGER_ENV_FILE", ENV_FILE_VALUES, HOSTINGER_ENV_FILE)
            hostinger_app_dir = resolve_env("HOSTINGER_APP_DIR", ENV_FILE_VALUES, HOSTINGER_APP_DIR)
            auto_link_hostinger = env_flag("AUTO_LINK_HOSTINGER", ENV_FILE_VALUES)
            if auto_link_hostinger:
                print("\nLinking Hostinger API to the new pod...")
                link_hostinger_to_pod(pod["id"], ENV_FILE_VALUES)
                print("  Hostinger env updated and API restarted.")
            print(f"\nNext steps:")
            if auto_link_hostinger:
                print(f"  1. Watch bootstrap (once Tailscale connects):")
                print(f"     ssh {hostinger_ssh_target} 'tailscale status'  # wait for airco-gpu peer")
            else:
                print(f"  1. Update RUNPOD_POD_ID on the VPS:")
                print(
                    f"     ssh {hostinger_ssh_target} "
                    f"'sed -i \"s/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID={pod['id']}/\" {hostinger_env_file}'"
                )
                print(f"  2. Restart the API:")
                print(
                    f"     ssh {hostinger_ssh_target} "
                    f"'cd {hostinger_app_dir} && "
                    f"docker compose -f docker-compose.cpu.yml --env-file .env.production restart api'"
                )
                print(f"  3. Watch bootstrap (once Tailscale connects):")
                print(f"     ssh {hostinger_ssh_target} 'tailscale status'  # wait for airco-gpu peer")
            return
        else:
            msg = (errors or [{}])[0].get("message", "unknown error")
            print(f"failed: {msg}")

    print("\nCould not deploy on any GPU type. Check RunPod dashboard for availability.")
    sys.exit(1)


if __name__ == "__main__":
    main()
