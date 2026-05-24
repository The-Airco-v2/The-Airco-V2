from pathlib import Path


def test_hostinger_deploy_stages_files_without_git_pull_on_vps():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-images.yml"
    ).read_text()

    assert "appleboy/scp-action@" in workflow
    assert "cd /app/airco" in workflow
    assert "git pull origin main" not in workflow
    assert "cp /app/airco/.gha-deploy/Backend/docker-compose.cpu.yml /app/airco/docker-compose.cpu.yml" in workflow


def test_manual_dispatch_uses_full_rebuild_flag_but_still_allows_deploy():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-images.yml"
    ).read_text()

    assert "full_rebuild:" in workflow
    assert "default: false" in workflow
    assert "github.event.inputs.full_rebuild == 'true'" in workflow
    assert "steps.filter.outputs.changed == 'true' || github.event_name == 'workflow_dispatch'" in workflow


def test_hostinger_deploy_runs_migrations_from_app_migrations_dir():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-images.yml"
    ).read_text()

    assert "cd /app/migrations && alembic -c alembic.ini upgrade head" in workflow
    assert "exec -T api alembic upgrade head" not in workflow


def test_runpod_deploy_workflow_uses_env_file_secret_and_script():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-runpod-pod.yml"
    ).read_text()

    assert "workflow_dispatch:" in workflow
    assert "RUNPOD_ENV_FILE" in workflow
    assert "Backend/.env.runpod" in workflow
    assert "python3 Backend/scripts/create_runpod_pod.py --env-file Backend/.env.runpod" in workflow
    assert "AUTO_LINK_HOSTINGER" in workflow
    assert "pod_id=" in workflow
    assert "appleboy/ssh-action@" in workflow
