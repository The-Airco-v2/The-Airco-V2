from __future__ import annotations

from datetime import timedelta


def test_get_presigned_url_can_emit_direct_public_object_url(monkeypatch):
    monkeypatch.setattr("airco.minio_client.settings.minio_bucket", "airco-evidence")
    monkeypatch.setattr("airco.minio_client.settings.minio_public_url", "http://localhost:9000")
    monkeypatch.setattr("airco.minio_client.settings.minio_public_presign", False)

    class FakeClient:
        def presigned_get_object(self, bucket: str, object_name: str, expires: timedelta):
            raise AssertionError("presigned_get_object should not be called in direct public mode")

    monkeypatch.setattr("airco.minio_client.get_minio", lambda: FakeClient())

    from airco.minio_client import get_presigned_url

    url = get_presigned_url("snapshots/session/cam/frame.jpg")

    assert url == "http://127.0.0.1:9000/airco-evidence/snapshots/session/cam/frame.jpg"


def test_get_presigned_url_keeps_presigned_mode_when_enabled(monkeypatch):
    monkeypatch.setattr("airco.minio_client.settings.minio_bucket", "airco-evidence")
    monkeypatch.setattr("airco.minio_client.settings.minio_public_url", "http://s3.example.com")
    monkeypatch.setattr("airco.minio_client.settings.minio_public_presign", True)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def presigned_get_object(self, bucket: str, object_name: str, expires: timedelta):
            assert bucket == "airco-evidence"
            assert object_name == "snapshots/session/cam/frame.jpg"
            return (
                "http://s3.example.com/airco-evidence/snapshots/session/cam/frame.jpg"
                "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=test"
            )

    monkeypatch.setattr("airco.minio_client.Minio", FakeClient)

    from airco.minio_client import get_presigned_url

    url = get_presigned_url("snapshots/session/cam/frame.jpg")

    assert url.startswith("http://s3.example.com/airco-evidence/snapshots/session/cam/frame.jpg")
    assert "X-Amz-Signature=test" in url
