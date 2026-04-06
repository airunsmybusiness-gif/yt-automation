"""Google Cloud Storage client wrapper."""

import base64
import logging
from pathlib import Path
from typing import Any

from google.cloud import storage

logger = logging.getLogger(__name__)


def get_storage_client() -> storage.Client:
    """Create a GCS client using default credentials."""
    return storage.Client()


def upload_string(
    bucket_name: str,
    blob_path: str,
    content: str,
    content_type: str = "application/jsonl",
) -> str:
    """Upload a string to GCS.

    Returns:
        The gs:// URI of the uploaded blob.
    """
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    uri = f"gs://{bucket_name}/{blob_path}"
    logger.info("Uploaded %d bytes to %s", len(content), uri)
    return uri


def upload_bytes(
    bucket_name: str,
    blob_path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to GCS. Returns gs:// URI."""
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=content_type)
    uri = f"gs://{bucket_name}/{blob_path}"
    logger.info("Uploaded %d bytes to %s", len(data), uri)
    return uri


def download_as_string(bucket_name: str, blob_path: str) -> str:
    """Download a blob as a UTF-8 string."""
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    return blob.download_as_text(encoding="utf-8")


def download_as_bytes(bucket_name: str, blob_path: str) -> bytes:
    """Download a blob as raw bytes."""
    client = get_storage_client()
    bucket = client.bucket(blob_path.split("/")[0] if "/" in blob_path else bucket_name)
    blob = client.bucket(bucket_name).blob(blob_path)
    return blob.download_as_bytes()


def list_blobs(
    bucket_name: str, prefix: str = ""
) -> list[dict[str, Any]]:
    """List blobs under a prefix.

    Returns:
        List of dicts with 'name', 'size', 'updated'.
    """
    client = get_storage_client()
    blobs = client.list_blobs(bucket_name, prefix=prefix)
    return [
        {"name": b.name, "size": b.size, "updated": str(b.updated)}
        for b in blobs
    ]


def ensure_bucket_exists(bucket_name: str, location: str = "us-central1") -> None:
    """Create a GCS bucket if it doesn't exist."""
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        client.create_bucket(bucket, location=location)
        logger.info("Created GCS bucket: %s", bucket_name)
