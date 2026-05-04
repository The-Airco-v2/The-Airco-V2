"""Regression tests for Savant container startup packaging."""

from pathlib import Path


def test_savant_dockerfile_passes_module_config_to_entrypoint():
    dockerfile = Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "Dockerfile"
    dockerfile_text = dockerfile.read_text(encoding="utf-8")

    assert 'COPY module.yml .' in dockerfile_text
    assert 'CMD ["/opt/savant/module/module.yml"]' in dockerfile_text

