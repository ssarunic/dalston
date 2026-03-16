"""Presigned URL generation for task I/O transport (M77).

The orchestrator generates presigned GET/PUT URLs at dispatch time so engines
can fetch input and store output over plain HTTP with no S3 credentials.

Both functions accept an ``s3://bucket/key`` URI and produce a time-limited
HTTPS (or HTTP for MinIO) URL using the same credential and endpoint
configuration as the rest of the orchestrator.

TTL default is 7 days (604 800 s). This is intentionally generous: the real
security boundary is "no permanent credentials in engines", which presigned
URLs already enforce. Short TTLs add operational complexity without narrowing
the attack surface for internal cluster traffic.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import boto3


def _make_s3_client():
    """Build a sync boto3 S3 client from environment variables.

    Uses the same env-var convention as ``dalston.engine_sdk.io``:
        DALSTON_S3_ENDPOINT_URL  – custom endpoint (required for MinIO)
        DALSTON_S3_REGION        – AWS region (default: eu-west-2)
        AWS_ACCESS_KEY_ID        – access key
        AWS_SECRET_ACCESS_KEY    – secret key
    """
    endpoint_url = os.environ.get("DALSTON_S3_ENDPOINT_URL")
    region = os.environ.get("DALSTON_S3_REGION", "eu-west-2")

    kwargs: dict = {"region_name": region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    return boto3.client("s3", **kwargs)


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` into ``(bucket, key)``."""
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {s3_uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def generate_get_url(s3_uri: str, ttl_seconds: int = 604800) -> str:
    """Generate a presigned GET URL for an existing S3 object.

    Args:
        s3_uri: Object location in ``s3://bucket/key`` format.
        ttl_seconds: URL lifetime (default 7 days).

    Returns:
        Presigned HTTPS/HTTP URL valid for *ttl_seconds*.
    """
    bucket, key = _parse_s3_uri(s3_uri)
    client = _make_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )


def generate_put_url(s3_uri: str, ttl_seconds: int = 604800) -> str:
    """Generate a presigned PUT URL for a not-yet-existing S3 object.

    Args:
        s3_uri: Destination location in ``s3://bucket/key`` format.
        ttl_seconds: URL lifetime (default 7 days).

    Returns:
        Presigned HTTPS/HTTP URL valid for *ttl_seconds*.
    """
    bucket, key = _parse_s3_uri(s3_uri)
    client = _make_s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
