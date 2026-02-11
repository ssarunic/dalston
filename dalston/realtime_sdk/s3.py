"""S3 client factory for realtime workers.

Provides async S3 client for audio recording and transcript storage.
Uses aioboto3 for async operations compatible with SessionHandler.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aioboto3

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

# Cached session for connection pooling
_session: aioboto3.Session | None = None


def _get_session() -> aioboto3.Session:
    """Get or create aioboto3 session."""
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


@asynccontextmanager
async def get_s3_client(
    bucket: str | None = None,
    endpoint_url: str | None = None,
) -> AsyncIterator[S3Client]:
    """Async context manager for S3 client.

    Uses environment variables for configuration:
        S3_REGION: AWS region (default: us-east-1)
        S3_ENDPOINT_URL: Custom endpoint for MinIO (if endpoint_url not provided)
        AWS_ACCESS_KEY_ID: Access key
        AWS_SECRET_ACCESS_KEY: Secret key

    Args:
        bucket: Bucket name (unused, for compatibility)
        endpoint_url: Custom S3 endpoint, overrides S3_ENDPOINT_URL env var

    Yields:
        Async S3 client
    """
    session = _get_session()

    kwargs: dict = {
        "region_name": os.environ.get("S3_REGION", "us-east-1"),
    }

    # Endpoint URL (MinIO or custom)
    final_endpoint = endpoint_url or os.environ.get("S3_ENDPOINT_URL")
    if final_endpoint:
        kwargs["endpoint_url"] = final_endpoint

    # Credentials
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    async with session.client("s3", **kwargs) as client:
        yield client
