"""S3-backed model storage for engines.

Models are stored in S3 as the canonical source. Engines download models
from S3 to local SSD cache on first use, then serve locally.

S3 structure:
    s3://{bucket}/models/{model_id}/
        model.bin
        config.json
        ...
        .complete  # Marker file indicating upload is complete

Local cache structure:
    {cache_dir}/{model_id}/
        model.bin
        config.json
        ...
        .complete  # Marker file indicating download is complete
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from dalston.engine_sdk.io import get_s3_client
from dalston.engine_sdk.model_paths import MODEL_BASE

logger = structlog.get_logger()

# Marker file indicating a complete model download/upload
COMPLETE_MARKER = ".complete"

# Default S3 prefix for models
MODELS_PREFIX = "models"


@dataclass
class CachedModelInfo:
    """Information about a locally cached model."""

    model_id: str
    path: Path
    size_bytes: int


class ModelNotInS3Error(Exception):
    """Raised when a model is not available in S3."""

    def __init__(self, model_id: str, bucket: str) -> None:
        self.model_id = model_id
        self.bucket = bucket
        super().__init__(f"Model {model_id} not found in s3://{bucket}/models/")


class S3ModelStorage:
    """S3-backed model storage with local caching.

    Downloads models from S3 to local SSD on first use. Subsequent requests
    are served from local cache.

    Usage:
        storage = S3ModelStorage.from_env()
        local_path = storage.ensure_local("Systran/faster-whisper-large-v3")
        # Use model from local_path
    """

    def __init__(
        self,
        bucket: str,
        local_cache_dir: Path | None = None,
        s3_prefix: str = MODELS_PREFIX,
    ) -> None:
        """Initialize S3 model storage.

        Args:
            bucket: S3 bucket name
            local_cache_dir: Local directory for caching models
                            (default: {MODEL_BASE}/s3-cache)
            s3_prefix: S3 key prefix for models (default: "models")
        """
        self.bucket = bucket
        self.s3_prefix = s3_prefix
        self.local_cache_dir = local_cache_dir or (MODEL_BASE / "s3-cache")
        self.local_cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> S3ModelStorage:
        """Create storage instance from environment variables.

        Environment variables:
            DALSTON_S3_BUCKET: S3 bucket name (required)
            DALSTON_MODEL_CACHE_DIR: Local cache directory (optional)
        """
        bucket = os.environ.get("DALSTON_S3_BUCKET")
        if not bucket:
            raise ValueError("DALSTON_S3_BUCKET environment variable is required")

        cache_dir = os.environ.get("DALSTON_MODEL_CACHE_DIR")
        local_cache = Path(cache_dir) if cache_dir else None

        return cls(bucket=bucket, local_cache_dir=local_cache)

    def _get_s3_key(self, model_id: str) -> str:
        """Get the S3 key prefix for a model."""
        # model_id may contain slashes (e.g., "Systran/faster-whisper-large-v3")
        return f"{self.s3_prefix}/{model_id}/"

    def _get_local_path(self, model_id: str) -> Path:
        """Get the local cache path for a model."""
        # Replace slashes with double-dash for filesystem safety
        safe_id = model_id.replace("/", "--")
        return self.local_cache_dir / safe_id

    def is_cached_locally(self, model_id: str) -> bool:
        """Check if a model is fully cached locally.

        Returns True only if the model directory exists AND has the
        .complete marker, indicating a successful previous download.
        """
        local_path = self._get_local_path(model_id)
        return (local_path / COMPLETE_MARKER).exists()

    def is_in_s3(self, model_id: str) -> bool:
        """Check if a model exists in S3.

        Returns True if the .complete marker exists in S3, indicating
        a fully uploaded model.
        """
        from botocore.exceptions import ClientError

        s3 = get_s3_client()
        key = f"{self._get_s3_key(model_id)}{COMPLETE_MARKER}"

        try:
            s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def ensure_local(self, model_id: str) -> Path:
        """Ensure model is available locally, downloading from S3 if needed.

        Args:
            model_id: Model identifier (e.g., "Systran/faster-whisper-large-v3")

        Returns:
            Path to local model directory

        Raises:
            ModelNotInS3Error: If model is not available in S3
            OSError: If download fails or disk is full
        """
        local_path = self._get_local_path(model_id)

        # Check if already cached
        if self.is_cached_locally(model_id):
            logger.debug("model_cache_hit", model_id=model_id, path=str(local_path))
            return local_path

        # Download from S3
        logger.info("model_cache_miss", model_id=model_id, downloading_from="s3")
        self._download_from_s3(model_id, local_path)

        return local_path

    def _download_from_s3(self, model_id: str, local_path: Path) -> None:
        """Download a model from S3 to local path.

        Downloads to a temp directory first, then moves to final location
        to ensure atomic completion.
        """
        s3 = get_s3_client()
        s3_prefix = self._get_s3_key(model_id)

        # List all objects with this prefix
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix)

        files_to_download: list[tuple[str, str]] = []
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Get relative path from model prefix
                relative = key[len(s3_prefix) :]
                if relative and relative != COMPLETE_MARKER:
                    files_to_download.append((key, relative))

        if not files_to_download:
            raise ModelNotInS3Error(model_id, self.bucket)

        # Download to temp directory first
        temp_dir = Path(tempfile.mkdtemp(prefix="dalston-model-"))
        try:
            total_size = 0
            for s3_key, relative_path in files_to_download:
                local_file = temp_dir / relative_path
                local_file.parent.mkdir(parents=True, exist_ok=True)

                logger.debug(
                    "downloading_model_file",
                    model_id=model_id,
                    file=relative_path,
                )
                s3.download_file(self.bucket, s3_key, str(local_file))
                total_size += local_file.stat().st_size

            # Create .complete marker in temp dir
            (temp_dir / COMPLETE_MARKER).touch()

            # Atomic move to final location
            if local_path.exists():
                shutil.rmtree(local_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_dir), str(local_path))

            logger.info(
                "model_downloaded",
                model_id=model_id,
                size_mb=round(total_size / 1024 / 1024, 1),
                path=str(local_path),
            )

        except Exception:
            # Clean up temp dir on failure
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def get_cached_models(self) -> list[CachedModelInfo]:
        """List all models in the local cache.

        Only returns fully downloaded models (those with .complete marker).
        """
        if not self.local_cache_dir.exists():
            return []

        models: list[CachedModelInfo] = []
        for entry in self.local_cache_dir.iterdir():
            if entry.is_dir() and (entry / COMPLETE_MARKER).exists():
                # Calculate size
                size = sum(
                    f.stat().st_size
                    for f in entry.rglob("*")
                    if f.is_file() and f.name != COMPLETE_MARKER
                )
                # Convert safe_id back to model_id
                model_id = entry.name.replace("--", "/")
                models.append(
                    CachedModelInfo(
                        model_id=model_id,
                        path=entry,
                        size_bytes=size,
                    )
                )

        return models

    def get_cache_stats(self) -> dict:
        """Get statistics about the local cache for heartbeat reporting."""
        models = self.get_cached_models()
        total_size = sum(m.size_bytes for m in models)

        return {
            "models": [m.model_id for m in models],
            "total_size_mb": round(total_size / 1024 / 1024, 1),
            "model_count": len(models),
        }

    def remove_local(self, model_id: str) -> bool:
        """Remove a model from local cache.

        Args:
            model_id: Model identifier

        Returns:
            True if model was removed, False if it wasn't cached
        """
        local_path = self._get_local_path(model_id)
        if local_path.exists():
            shutil.rmtree(local_path)
            logger.info("model_removed_from_cache", model_id=model_id)
            return True
        return False

    def clear_cache(self) -> int:
        """Remove all models from local cache.

        Returns:
            Number of models removed
        """
        models = self.get_cached_models()
        for model in models:
            shutil.rmtree(model.path, ignore_errors=True)

        logger.info("cache_cleared", models_removed=len(models))
        return len(models)
