"""Async S3 client factory using aioboto3."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aioboto3

from dalston.config import Settings, get_settings

if TYPE_CHECKING:
    pass

_session: aioboto3.Session | None = None


def _get_session() -> aioboto3.Session:
    """Get or create aioboto3 session."""
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


@asynccontextmanager
async def get_s3_client(settings: Settings | None = None):
    """Async context manager for S3 client.

    Usage:
        async with get_s3_client() as s3:
            await s3.put_object(...)
    """
    if settings is None:
        settings = get_settings()

    session = _get_session()

    # Build client kwargs
    kwargs: dict = {
        "region_name": settings.s3_region,
    }

    # Use custom endpoint for MinIO local dev
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url

    # Use explicit credentials if provided
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

    async with session.client("s3", **kwargs) as client:
        yield client


async def ensure_bucket_exists(settings: Settings | None = None) -> None:
    """Ensure the S3 bucket exists, create if not."""
    if settings is None:
        settings = get_settings()

    async with get_s3_client(settings) as s3:
        try:
            await s3.head_bucket(Bucket=settings.s3_bucket)
        except Exception:
            # Bucket doesn't exist, create it
            await s3.create_bucket(Bucket=settings.s3_bucket)
