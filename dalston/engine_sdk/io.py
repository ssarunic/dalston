"""Task I/O helpers for S3 download/upload operations.

Engines use synchronous boto3 for simpler processing loops.
Files are downloaded to local temp, processed, then results uploaded.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config


def get_s3_client():
    """Create a boto3 S3 client using environment variables.

    Environment variables:
        S3_ENDPOINT_URL: Custom endpoint (e.g., MinIO for local dev)
        S3_REGION: AWS region (default: us-east-1)
        AWS_ACCESS_KEY_ID: AWS access key
        AWS_SECRET_ACCESS_KEY: AWS secret key
    """
    endpoint_url = os.environ.get("S3_ENDPOINT_URL")
    region = os.environ.get("S3_REGION", "us-east-1")

    config = Config(
        retries={"max_attempts": 3, "mode": "standard"},
    )

    kwargs: dict[str, Any] = {
        "region_name": region,
        "config": config,
    }

    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    return boto3.client("s3", **kwargs)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an S3 URI into bucket and key.

    Args:
        uri: S3 URI in format s3://bucket/key/path

    Returns:
        Tuple of (bucket, key)

    Raises:
        ValueError: If URI is not a valid S3 URI
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {uri}")

    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def download_file(s3_uri: str, local_path: Path | None = None) -> Path:
    """Download a file from S3 to local storage.

    Args:
        s3_uri: S3 URI to download from
        local_path: Local path to save to (auto-generated if None)

    Returns:
        Path to downloaded file
    """
    bucket, key = parse_s3_uri(s3_uri)
    s3 = get_s3_client()

    if local_path is None:
        # Create temp file with same extension
        suffix = Path(key).suffix or ".tmp"
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        local_path = Path(temp_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(local_path))

    return local_path


def upload_file(local_path: Path, s3_uri: str) -> str:
    """Upload a file from local storage to S3.

    Args:
        local_path: Local file path to upload
        s3_uri: S3 URI to upload to

    Returns:
        The S3 URI of the uploaded file
    """
    bucket, key = parse_s3_uri(s3_uri)
    s3 = get_s3_client()

    s3.upload_file(str(local_path), bucket, key)

    return s3_uri


def download_json(s3_uri: str) -> dict[str, Any]:
    """Download and parse a JSON file from S3.

    Args:
        s3_uri: S3 URI to the JSON file

    Returns:
        Parsed JSON as a dictionary
    """
    bucket, key = parse_s3_uri(s3_uri)
    s3 = get_s3_client()

    response = s3.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read().decode("utf-8")

    return json.loads(content)


def upload_json(data: dict[str, Any], s3_uri: str) -> str:
    """Upload a dictionary as JSON to S3.

    Args:
        data: Dictionary to serialize and upload
        s3_uri: S3 URI to upload to

    Returns:
        The S3 URI of the uploaded file
    """
    bucket, key = parse_s3_uri(s3_uri)
    s3 = get_s3_client()

    content = json.dumps(data, indent=2, default=str)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType="application/json",
    )

    return s3_uri


def build_task_input_uri(bucket: str, job_id: str, task_id: str) -> str:
    """Build the S3 URI for a task's input.json file.

    Args:
        bucket: S3 bucket name
        job_id: Job identifier
        task_id: Task identifier

    Returns:
        S3 URI in format s3://bucket/jobs/{job_id}/tasks/{task_id}/input.json
    """
    return f"s3://{bucket}/jobs/{job_id}/tasks/{task_id}/input.json"


def build_task_output_uri(bucket: str, job_id: str, task_id: str) -> str:
    """Build the S3 URI for a task's output.json file.

    Args:
        bucket: S3 bucket name
        job_id: Job identifier
        task_id: Task identifier

    Returns:
        S3 URI in format s3://bucket/jobs/{job_id}/tasks/{task_id}/output.json
    """
    return f"s3://{bucket}/jobs/{job_id}/tasks/{task_id}/output.json"
