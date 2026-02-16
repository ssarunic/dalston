"""S3 storage service for audio files and transcripts."""

import json
import mimetypes
from pathlib import Path
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import UploadFile

from dalston.common.s3 import get_s3_client
from dalston.config import Settings


class StorageService:
    """Service for S3 storage operations."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.s3_bucket

    async def upload_audio(
        self,
        job_id: UUID,
        file: UploadFile | None = None,
        file_content: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Upload audio file to S3.

        Args:
            job_id: Job UUID for path construction
            file: Uploaded file from FastAPI (optional if file_content provided)
            file_content: Pre-read file content (required if file is None)
            filename: Explicit filename (used when file is None)
            content_type: Explicit content type (used when file is None)

        Returns:
            S3 URI: s3://{bucket}/jobs/{job_id}/audio/original.{ext}
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

        # Build S3 key
        key = f"jobs/{job_id}/audio/original.{ext}"

        # Use provided content or read from file
        if file_content is not None:
            content = file_content
        elif file is not None:
            content = await file.read()
        else:
            raise ValueError("Either file or file_content must be provided")

        # Upload to S3
        async with get_s3_client(self.settings) as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=resolved_content_type,
            )

        return f"s3://{self.bucket}/{key}"

    async def get_transcript(self, job_id: UUID) -> dict[str, Any] | None:
        """Fetch transcript JSON from S3 if it exists.

        Args:
            job_id: Job UUID

        Returns:
            Parsed transcript dict or None if not found
        """
        key = f"jobs/{job_id}/transcript.json"

        async with get_s3_client(self.settings) as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                body = await response["Body"].read()
                return json.loads(body.decode("utf-8"))
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    return None
                raise

    async def delete_job_artifacts(self, job_id: UUID) -> None:
        """Delete all S3 artifacts for a job.

        Deletes: audio/*, tasks/*, transcript.json

        Args:
            job_id: Job UUID
        """
        prefix = f"jobs/{job_id}/"

        async with get_s3_client(self.settings) as s3:
            # List all objects with prefix
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                if "Contents" not in page:
                    continue

                # Delete objects
                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                if objects:
                    await s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": objects},
                    )

    async def delete_job_audio(self, job_id: UUID) -> None:
        """Delete audio files for a job.

        Deletes: audio/* (preserves tasks/* and transcript.json)

        Args:
            job_id: Job UUID
        """
        prefix = f"jobs/{job_id}/audio/"

        async with get_s3_client(self.settings) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                if "Contents" not in page:
                    continue

                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                if objects:
                    await s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": objects},
                    )

    async def delete_session_artifacts(self, session_id: UUID) -> None:
        """Delete all S3 artifacts for a realtime session.

        Args:
            session_id: Session UUID
        """
        prefix = f"sessions/{session_id}/"

        async with get_s3_client(self.settings) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                if "Contents" not in page:
                    continue

                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                if objects:
                    await s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": objects},
                    )

    async def has_audio(self, job_id: UUID) -> bool:
        """Check if audio exists for a job.

        Args:
            job_id: Job UUID

        Returns:
            True if audio files exist
        """
        prefix = f"jobs/{job_id}/audio/"

        async with get_s3_client(self.settings) as s3:
            response = await s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=1,
            )
            return response.get("KeyCount", 0) > 0

    async def get_task_input(
        self, job_id: UUID, task_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch task input JSON from S3.

        Args:
            job_id: Job UUID
            task_id: Task UUID

        Returns:
            Parsed input dict or None if not found
        """
        key = f"jobs/{job_id}/tasks/{task_id}/input.json"

        async with get_s3_client(self.settings) as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                body = await response["Body"].read()
                return json.loads(body.decode("utf-8"))
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    return None
                raise

    async def get_task_output(
        self, job_id: UUID, task_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch task output JSON from S3.

        Args:
            job_id: Job UUID
            task_id: Task UUID

        Returns:
            Parsed output dict or None if not found
        """
        key = f"jobs/{job_id}/tasks/{task_id}/output.json"

        async with get_s3_client(self.settings) as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                body = await response["Body"].read()
                return json.loads(body.decode("utf-8"))
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    return None
                raise
