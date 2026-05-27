from pathlib import Path


def test_runpod_bootstrap_image_sets_podman_in_container_env():
    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "Dockerfile.runpod-bootstrap"
    ).read_text()

    assert "uidmap" in dockerfile
    assert "slirp4netns" in dockerfile
    assert "fuse-overlayfs" in dockerfile
    assert 'ENV _CONTAINERS_USERNS_CONFIGURED="done"' in dockerfile
    assert "ENV BUILDAH_ISOLATION=chroot" in dockerfile
    assert "ENV container=oci" in dockerfile
