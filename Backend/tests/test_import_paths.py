"""Test that the v2 pytest harness exposes service package import roots."""

import importlib


def test_service_packages_are_importable_from_tests():
    analytics = importlib.import_module("analytics_consumer.alerts")
    identity = importlib.import_module("identity_consumer.cross_camera")

    assert analytics.AlertGenerator is not None
    assert identity.CrossCameraMerger is not None
