from pathlib import Path


def test_bootstrap_entrypoint_uses_userspace_tailscale():
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "bootstrap-entrypoint.sh"
    ).read_text()

    assert "--tun=userspace-networking" in script
    assert 'TAILSCALE_SOCKET=/tmp/tailscaled.sock' in script
    assert 'tailscale --socket="${TAILSCALE_SOCKET}" up' in script
    assert 'cat /tmp/tailscaled.log || true' in script
