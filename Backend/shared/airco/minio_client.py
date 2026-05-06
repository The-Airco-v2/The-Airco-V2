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
EMPLOYEE_BUCKET_NAME = "airco-employee"
EMPLOYEE_FACE_PREFIX = "employee-faces"


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


def delete_object(object_name: str) -> None:
    """Delete a single object from the default MinIO bucket."""
    client = get_minio()
    try:
        client.remove_object(settings.minio_bucket, object_name)
    except Exception as e:
        logger.warning("Failed to delete object %s from bucket %s: %s", object_name, settings.minio_bucket, e)


def upload_employee_face(
    object_name: str,
    data: bytes,
    content_type: str = "image/jpeg",
) -> str:
    """Upload employee face image to airco-employee bucket. Returns the object name (path)."""
    client = get_minio()
    # Ensure employee bucket exists
    if not client.bucket_exists(EMPLOYEE_BUCKET_NAME):
        client.make_bucket(EMPLOYEE_BUCKET_NAME)
    
    # Prefix with employee-faces/ for organization
    prefixed_object_name = f"{EMPLOYEE_FACE_PREFIX}/{object_name.lstrip('/')}"
    
    client.put_object(
        EMPLOYEE_BUCKET_NAME,
        prefixed_object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return prefixed_object_name


def delete_employee_face(object_name: str) -> None:
    """Delete employee face image from airco-employee bucket."""
    client = get_minio()
    try:
        prefixed_object_name = object_name if object_name.startswith(f"{EMPLOYEE_FACE_PREFIX}/") else f"{EMPLOYEE_FACE_PREFIX}/{object_name.lstrip('/')}"
        client.remove_object(EMPLOYEE_BUCKET_NAME, prefixed_object_name)
    except Exception as e:
        logger.warning("Failed to delete employee face %s from bucket %s: %s", object_name, EMPLOYEE_BUCKET_NAME, e)


def delete_objects_by_prefix(prefix: str) -> None:
    """Delete all objects with given prefix from the default bucket."""
    client = get_minio()
    try:
        objects = client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)
        for obj in objects:
            client.remove_object(settings.minio_bucket, obj.object_name)
    except Exception as e:
        logger.warning("Failed to delete objects with prefix %s from bucket %s: %s", prefix, settings.minio_bucket, e)


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
