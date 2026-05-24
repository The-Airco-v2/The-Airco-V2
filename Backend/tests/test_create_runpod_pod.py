import importlib.util
import os
from pathlib import Path


def _load_create_runpod_pod_module():
    script_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "create_runpod_pod.py"
    )
    spec = importlib.util.spec_from_file_location("create_runpod_pod", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_pod_config_omits_registry_auth_when_not_configured():
    module = _load_create_runpod_pod_module()
    module.RUNPOD_CONTAINER_REGISTRY_AUTH_ID = ""

    config = module.build_pod_config("NVIDIA GeForce RTX 3090")

    assert "containerRegistryAuthId" not in config


def test_build_pod_config_includes_registry_auth_when_configured():
    module = _load_create_runpod_pod_module()
    module.RUNPOD_CONTAINER_REGISTRY_AUTH_ID = "clzdaifot0001l90809257ynb"

    config = module.build_pod_config("NVIDIA GeForce RTX 3090")

    assert config["containerRegistryAuthId"] == "clzdaifot0001l90809257ynb"


def test_link_hostinger_to_pod_updates_env_and_restarts_api(monkeypatch):
    module = _load_create_runpod_pod_module()
    calls = []

    def fake_run(cmd, check):
        calls.append((cmd, check))

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.HOSTINGER_SSH_TARGET = "root@example.com"
    module.HOSTINGER_ENV_FILE = "/app/airco/.env.production"
    module.HOSTINGER_APP_DIR = "/app/airco"

    module.link_hostinger_to_pod("pod-123")

    assert calls == [
        (
            [
                "ssh",
                "root@example.com",
                "sed -i 's/^RUNPOD_POD_ID=.*/RUNPOD_POD_ID=pod-123/' /app/airco/.env.production",
            ],
            True,
        ),
        (
            [
                "ssh",
                "root@example.com",
                "cd /app/airco && docker compose -f docker-compose.cpu.yml --env-file .env.production restart api",
            ],
            True,
        ),
    ]


def test_load_env_file_parses_simple_key_value_pairs(tmp_path):
    module = _load_create_runpod_pod_module()
    env_file = tmp_path / "runpod.env"
    env_file.write_text(
        "# comment\n"
        "RUNPOD_API_KEY=from-file\n"
        "GHCR_PAT=ghcr-file\n"
        "PUBLIC_MINIO_URL=https://api.example.com/minio\n"
    )

    loaded = module.load_env_file(env_file)

    assert loaded == {
        "RUNPOD_API_KEY": "from-file",
        "GHCR_PAT": "ghcr-file",
        "PUBLIC_MINIO_URL": "https://api.example.com/minio",
    }


def test_resolve_env_prefers_shell_env_over_env_file(monkeypatch):
    module = _load_create_runpod_pod_module()
    monkeypatch.setenv("RUNPOD_API_KEY", "from-shell")

    value = module.resolve_env("RUNPOD_API_KEY", {"RUNPOD_API_KEY": "from-file"})

    assert value == "from-shell"


def test_resolve_env_uses_env_file_when_shell_env_missing(monkeypatch):
    module = _load_create_runpod_pod_module()
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)

    value = module.resolve_env("RUNPOD_API_KEY", {"RUNPOD_API_KEY": "from-file"})

    assert value == "from-file"
