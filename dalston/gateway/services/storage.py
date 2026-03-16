"""Artifact storage service for audio files, task payloads, and transcripts."""

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import UploadFile

from dalston.common.s3 import get_s3_client
from dalston.common.timeouts import S3_PRESIGNED_URL_EXPIRY_SECONDS
from dalston.config import Settings
from dalston.gateway.services.artifact_store import build_artifact_store


class StorageService:
    """Service for artifact storage operations."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.s3_bucket
        self.artifact_store = build_artifact_store(settings)

    async def upload_audio(
        self,
        job_id: UUID,
        file: UploadFile | None = None,
        file_content: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Upload audio file to the configured artifact backend.

        Args:
            job_id: Job UUID for path construction
            file: Uploaded file from FastAPI (optional if file_content provided)
            file_content: Pre-read file content (required if file is None)
            filename: Explicit filename (used when file is None)
            content_type: Explicit content type (used when file is None)

        Returns:
            Artifact URI (S3 in distributed mode, file URI in lite mode)
        """
        # Resolve filename
        resolved_filename = filename
        if resolved_filename is None and file is not None:
            resolved_filename = file.filename

        # Determine file extension
        ext = "bin"
        if resolved_filename:
            ext = Path(resolved_filename).suffix.lstrip(".") or "bin"

        # Determine content type
        resolved_content_type = content_type
        if resolved_content_type is None and file is not None:
            resolved_content_type = file.content_type
        if not resolved_content_type and resolved_filename:
            resolved_content_type, _ = mimetypes.guess_type(resolved_filename)
        resolved_content_type = resolved_content_type or "application/octet-stream"

        # Build canonical artifact key
        key = f"jobs/{job_id}/audio/original.{ext}"

        # Use provided content or read from file
        if file_content is not None:
            content = file_content
        elif file is not None:
            content = await file.read()
        else:
            raise ValueError("Either file or file_content must be provided")

        return await self.artifact_store.write_bytes(
            key=key,
            payload=content,
            content_type=resolved_content_type,
        )

    async def get_transcript(self, job_id: UUID) -> dict[str, Any] | None:
        """Fetch transcript JSON if it exists.

        Args:
            job_id: Job UUID

        Returns:
            Parsed transcript dict or None if not found
        """
        key = f"jobs/{job_id}/transcript.json"
        uri = await self.artifact_store.uri_for_key(key)
        try:
            body = await self.artifact_store.read_bytes(uri)
        except FileNotFoundError:
            return None
        return json.loads(body.decode("utf-8"))

    async def delete_job_artifacts(self, job_id: UUID) -> None:
        """Delete all artifacts for a job.

        Deletes: audio/*, tasks/*, transcript.json

        Args:
            job_id: Job UUID
        """
        prefix = f"jobs/{job_id}/"
        await self.artifact_store.delete_prefix(prefix)

    async def delete_job_audio(self, job_id: UUID) -> None:
        """Delete audio files for a job.

        Deletes: audio/* (preserves tasks/* and transcript.json)

        Args:
            job_id: Job UUID
        """
        prefix = f"jobs/{job_id}/audio/"
        await self.artifact_store.delete_prefix(prefix)

    async def delete_session_artifacts(self, session_id: UUID) -> None:
        """Delete all artifacts for a realtime session.

        Args:
            session_id: Session UUID
        """
        prefix = f"sessions/{session_id}/"
        await self.artifact_store.delete_prefix(prefix)

    async def has_audio(self, job_id: UUID) -> bool:
        """Check if audio exists for a job.

        Args:
            job_id: Job UUID

        Returns:
            True if audio files exist
        """
        prefix = f"jobs/{job_id}/audio/"
        return await self.artifact_store.has_prefix(prefix)

    async def generate_presigned_url(
        self, key: str, expires_in: int = S3_PRESIGNED_URL_EXPIRY_SECONDS
    ) -> str:
        """Generate a presigned URL for downloading an S3 object.

        Args:
            key: S3 object key
            expires_in: URL expiration time in seconds (default 1 hour)

        Returns:
            Presigned URL for GET request
        """
        return await self.generate_presigned_url_for_bucket(
            bucket=self.bucket,
            key=key,
            expires_in=expires_in,
        )

    def parse_s3_uri(self, s3_uri: str) -> tuple[str, str]:
        """Parse s3://bucket/key URI into (bucket, key) tuple.

        Args:
            s3_uri: S3 URI in format s3://bucket/key

        Returns:
            Tuple of (bucket, key)

        Raises:
            ValueError: If URI is invalid or malformed
        """
        if not s3_uri or not s3_uri.startswith("s3://"):
            raise ValueError("Invalid S3 URI")
        uri_parts = s3_uri[5:].split("/", 1)  # Skip "s3://"
        if len(uri_parts) != 2 or not uri_parts[0] or not uri_parts[1]:
            raise ValueError("Invalid S3 URI format")
        return uri_parts[0], uri_parts[1]

    async def generate_presigned_url_from_uri(
        self,
        s3_uri: str,
        expires_in: int = S3_PRESIGNED_URL_EXPIRY_SECONDS,
        require_expected_bucket: bool = True,
    ) -> str:
        """Generate a presigned URL directly from an s3:// URI.

        Args:
            s3_uri: S3 URI in format s3://bucket/key
            expires_in: URL expiration time in seconds (default 1 hour)
            require_expected_bucket: If True, validates bucket matches configured bucket

        Returns:
            Presigned URL for GET request

        Raises:
            ValueError: If URI is invalid or bucket doesn't match (when required)
        """
        bucket, key = self.parse_s3_uri(s3_uri)
        if require_expected_bucket and bucket != self.bucket:
            raise ValueError(f"Bucket mismatch: expected {self.bucket}, got {bucket}")
        return await self.generate_presigned_url_for_bucket(bucket, key, expires_in)

    async def generate_presigned_url_for_bucket(
        self,
        bucket: str,
        key: str,
        expires_in: int = S3_PRESIGNED_URL_EXPIRY_SECONDS,
        endpoint_url_override: str | None = None,
    ) -> str:
        """Generate a presigned URL for downloading an S3 object in a bucket.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            expires_in: URL expiration time in seconds
            endpoint_url_override: Optional endpoint to use for URL signing

        Returns:
            Presigned URL for GET request
        """
        presign_endpoint = endpoint_url_override or self.resolve_presign_endpoint()
        async with get_s3_client(
            self.settings, endpoint_url_override=presign_endpoint
        ) as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )

    def resolve_presign_endpoint(self) -> str | None:
        """Resolve endpoint used for presigned URLs.

        Priority:
        1. Explicit S3_PUBLIC_ENDPOINT_URL (preferred).
        2. Local MinIO fallback: if internal endpoint host is `minio`,
           use `localhost` with same scheme/port for browser reachability.
        3. None (use internal S3 endpoint as-is).
        """
        if self.settings.s3_public_endpoint_url:
            return self.settings.s3_public_endpoint_url

        if not self.settings.s3_endpoint_url:
            return None

        parsed = urlsplit(self.settings.s3_endpoint_url)
        host = (parsed.hostname or "").lower()
        if host != "minio":
            return None

        scheme = parsed.scheme or "http"
        port = parsed.port or (443 if scheme == "https" else 80)
        default_port = (scheme == "https" and port == 443) or (
            scheme == "http" and port == 80
        )
        netloc = "localhost" if default_port else f"localhost:{port}"
        return urlunsplit((scheme, netloc, "", "", ""))

    async def object_exists(self, key: str) -> bool:
        """Check if a specific object exists.

        Args:
            key: S3 object key

        Returns:
            True if the object exists
        """
        uri = await self.artifact_store.uri_for_key(key)
        return await self.artifact_store.exists(uri)

    async def get_task_request(
        self, job_id: UUID, task_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch task request JSON.

        Args:
            job_id: Job UUID
            task_id: Task UUID

        Returns:
            Parsed request dict or None if not found
        """
        key = f"jobs/{job_id}/tasks/{task_id}/request.json"
        uri = await self.artifact_store.uri_for_key(key)
        try:
            body = await self.artifact_store.read_bytes(uri)
        except FileNotFoundError:
            return None
        return json.loads(body.decode("utf-8"))

    async def get_task_response(
        self, job_id: UUID, task_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch task response JSON.

        Args:
            job_id: Job UUID
            task_id: Task UUID

        Returns:
            Parsed response dict or None if not found
        """
        key = f"jobs/{job_id}/tasks/{task_id}/response.json"
        uri = await self.artifact_store.uri_for_key(key)
        try:
            body = await self.artifact_store.read_bytes(uri)
        except FileNotFoundError:
            return None
        return json.loads(body.decode("utf-8"))
