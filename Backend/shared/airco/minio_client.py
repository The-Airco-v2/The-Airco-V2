"""MinIO S3-compatible object storage client."""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from urllib.parse import quote, urlparse, urlunparse

from minio import Minio

from airco.config import settings

logger = logging.getLogger(__name__)

_client: Minio | None = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # Ensure bucket exists
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
    return _client


def upload_bytes(
    object_name: str,
    data: bytes,
    content_type: str = "image/jpeg",
) -> str:
    """Upload bytes to MinIO. Returns the object name (path)."""
    client = get_minio()
    client.put_object(
        settings.minio_bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_name


def get_presigned_url(object_name: str, expires: timedelta = timedelta(hours=1)) -> str:
    """Get a presigned URL for downloading an object."""
    public_base = (settings.minio_public_url or "").strip()
    if public_base:
        public_parts = urlparse(public_base)
        if public_parts.scheme and public_parts.netloc:
            if not settings.minio_public_presign or public_parts.hostname in {"localhost", "127.0.0.1", "host.docker.internal"}:
                object_path = "/".join(quote(part.strip("/"), safe="") for part in object_name.split("/") if part)
                public_host = public_parts.hostname or ""
                if public_host == "localhost":
                    public_host = "127.0.0.1"
                public_netloc = public_host
                if public_parts.port:
                    public_netloc = f"{public_host}:{public_parts.port}"
                path = f"/{settings.minio_bucket}/{object_path}"
                return urlunparse(
                    (
                        public_parts.scheme,
                        public_netloc,
                        path,
                        "",
                        "",
                        "",
                    )
                )

            public_client = Minio(
                public_parts.netloc,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=public_parts.scheme == "https",
            )
            return public_client.presigned_get_object(settings.minio_bucket, object_name, expires=expires)

    client = get_minio()
    return client.presigned_get_object(settings.minio_bucket, object_name, expires=expires)
