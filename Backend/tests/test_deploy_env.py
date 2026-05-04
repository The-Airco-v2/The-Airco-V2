import os
import subprocess
from pathlib import Path

from airco.deploy_env import REQUIRED_SECRET_KEYS, render_env


def test_render_env_overrides_secret_placeholders():
    template = (
        "TENANT_ID=default\n"
        "POSTGRES_PASSWORD=__FROM_GITHUB_SECRET__\n"
        "PUBLIC_API_URL=https://the-airco.net\n"
    )
    rendered = render_env(
        template,
        {
            "POSTGRES_PASSWORD": "real-db-pass",
            "MINIO_ACCESS_KEY": "minio-user",
            "MINIO_SECRET_KEY": "minio-pass",
            "CENTRIFUGO_API_KEY": "centrifugo-key",
            "CENTRIFUGO_TOKEN_SECRET": "centrifugo-secret",
            "SUPABASE_URL": "https://placeholder.supabase.co",
            "SUPABASE_ANON_KEY": "ci-anon-key",
            "SUPABASE_SERVICE_ROLE_KEY": "ci-service-key",
        },
    )
    assert "TENANT_ID=default" in rendered
    assert "PUBLIC_API_URL=https://the-airco.net" in rendered
    assert "POSTGRES_PASSWORD=real-db-pass" in rendered


def test_render_env_requires_all_secrets():
    template = "POSTGRES_PASSWORD=__FROM_GITHUB_SECRET__\n"
    try:
        render_env(template, {})
    except ValueError as exc:
        for key in REQUIRED_SECRET_KEYS:
            assert key in str(exc)
    else:
        raise AssertionError("expected ValueError for missing secrets")


def test_render_env_cli_runs_without_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    output_path = tmp_path / "airco.env"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "POSTGRES_PASSWORD": "prod-db-pass",
            "MINIO_ACCESS_KEY": "minio-user",
            "MINIO_SECRET_KEY": "minio-pass",
            "CENTRIFUGO_API_KEY": "centrifugo-key",
            "CENTRIFUGO_TOKEN_SECRET": "centrifugo-secret",
            "SUPABASE_URL": "https://placeholder.supabase.co",
            "SUPABASE_ANON_KEY": "ci-anon-key",
            "SUPABASE_SERVICE_ROLE_KEY": "ci-service-key",
        }
    )

    result = subprocess.run(
        [
            "./v2/scripts/render_env.py",
            "--template",
            "v2/.env.production.template",
            "--output",
            str(output_path),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = output_path.read_text()
    assert "POSTGRES_PASSWORD=prod-db-pass" in rendered
    assert "PUBLIC_API_URL=https://the-airco.net" in rendered
