"""Regression tests for Savant container startup packaging."""

from pathlib import Path


def test_savant_dockerfile_passes_module_config_to_entrypoint():
    dockerfile = Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "Dockerfile"
    dockerfile_text = dockerfile.read_text(encoding="utf-8")

    assert 'COPY module.yml .' in dockerfile_text
    assert 'COPY custom_pyfunc.py .' in dockerfile_text
    assert "COPY custom_pyfunc.py /opt/savant/gst_plugins/python/airco_custom_pyfunc.py" in dockerfile_text
    assert "COPY redis_sink.py /opt/savant/gst_plugins/python/redis_sink.py" in dockerfile_text
    assert "COPY event_utils.py /opt/savant/gst_plugins/python/event_utils.py" in dockerfile_text
    assert "COPY frame_artifacts.py /opt/savant/gst_plugins/python/frame_artifacts.py" in dockerfile_text
    assert "ENV PYTHONPATH=/opt/savant:/opt/savant/module" in dockerfile_text
    assert 'CMD ["/opt/savant/module/module.yml"]' in dockerfile_text


def test_savant_module_uses_unique_airco_pyfunc_name():
    module_path = Path(__file__).resolve().parent.parent / "services" / "savant-pipeline" / "module.yml"
    module_text = module_path.read_text(encoding="utf-8")

    assert "module: airco_custom_pyfunc" in module_text
    assert "module: custom_pyfunc" not in module_text
