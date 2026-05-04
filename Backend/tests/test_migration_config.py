from configparser import ConfigParser
from pathlib import Path
import re

from migrations.config import build_sqlalchemy_url


def test_build_sqlalchemy_url_uses_sync_driver(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "timescaledb")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "airco")
    monkeypatch.setenv("POSTGRES_USER", "airco")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    assert build_sqlalchemy_url() == "postgresql://airco:secret@timescaledb:5432/airco"


def test_alembic_ini_prepends_v2_root_for_migration_imports():
    parser = ConfigParser()
    parser.read(Path(__file__).resolve().parents[1] / "migrations" / "alembic.ini")

    assert parser.get("alembic", "prepend_sys_path") == ".."


def test_migration_revisions_are_unique():
    versions_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    revisions: dict[str, list[str]] = {}

    for migration_file in versions_dir.glob("*.py"):
        text = migration_file.read_text(encoding="utf-8")
        match = re.search(r'^revision = "([^"]+)"', text, flags=re.MULTILINE)
        if match is None:
            continue
        revisions.setdefault(match.group(1), []).append(migration_file.name)

    duplicates = {revision: files for revision, files in revisions.items() if len(files) > 1}
    assert duplicates == {}
