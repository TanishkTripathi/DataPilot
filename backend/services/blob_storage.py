"""Binary object storage abstraction.

The local backend is used for development. The GCS backend can be enabled later
by setting STORAGE_BACKEND=gcs and providing GCS_BUCKET_NAME.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parents[2]
LOCAL_ROOT = BASE_DIR / "storage" / "objects"


def _backend() -> str:
    return os.getenv("STORAGE_BACKEND", "local").strip().lower()


def _gcs_bucket_name() -> str:
    value = os.getenv("GCS_BUCKET_NAME", "").strip()
    if not value:
        raise RuntimeError("GCS_BUCKET_NAME is required when STORAGE_BACKEND=gcs")
    return value


def put_bytes(data: bytes, object_name: str, content_type: Optional[str] = None) -> str:
    """Store bytes and return a stable storage URI/path."""
    if _backend() == "gcs":
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        blob = bucket.blob(object_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{bucket.name}/{object_name}"

    path = LOCAL_ROOT / object_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.relative_to(BASE_DIR))


def get_bytes(object_name: str) -> bytes:
    """Read stored bytes by object name/path."""
    if _backend() == "gcs":
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        return bucket.blob(object_name).download_as_bytes()

    return (LOCAL_ROOT / object_name).read_bytes()


def delete_object(object_name: str) -> None:
    """Delete an object if it exists."""
    if _backend() == "gcs":
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        blob = bucket.blob(object_name)
        if blob.exists(client):
            blob.delete()
        return

    path = LOCAL_ROOT / object_name
    if path.exists():
        path.unlink()
