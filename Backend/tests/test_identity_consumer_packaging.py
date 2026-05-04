"""Regression tests for identity-consumer container packaging."""

from pathlib import Path


def test_identity_consumer_dockerfile_copies_package_to_match_entrypoint():
    dockerfile = Path(__file__).resolve().parent.parent / "services" / "identity-consumer" / "Dockerfile"
    dockerfile_text = dockerfile.read_text(encoding="utf-8")

    assert 'COPY services/identity-consumer/identity_consumer/ /app/identity_consumer/' in dockerfile_text
    assert 'CMD ["python", "-m", "identity_consumer.main"]' in dockerfile_text

