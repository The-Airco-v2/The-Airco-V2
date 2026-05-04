from __future__ import annotations

from pathlib import Path
import sys

V2_ULTIMATE_ADAPTER_ROOT = Path(__file__).resolve().parent.parent / "services" / "ultimate-adapter"
if str(V2_ULTIMATE_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ULTIMATE_ADAPTER_ROOT))
for _module_name in list(sys.modules):
    if _module_name == "ultimate_adapter" or _module_name.startswith("ultimate_adapter."):
        sys.modules.pop(_module_name, None)

from ultimate_adapter.config import load_settings  # noqa: E402


def test_load_settings_defaults_ultimate_device_to_gpu(monkeypatch):
    monkeypatch.delenv("ULTIMATE_DEVICE", raising=False)

    settings = load_settings()

    assert settings.ultimate_device == "cuda:0"


def test_load_settings_respects_explicit_ultimate_device_override(monkeypatch):
    monkeypatch.setenv("ULTIMATE_DEVICE", "cuda:1")

    settings = load_settings()

    assert settings.ultimate_device == "cuda:1"
