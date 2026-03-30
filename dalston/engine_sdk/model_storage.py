"""Multi-source model storage for engines.

Engines can download models from multiple sources:
- S3 (production default): s3://{bucket}/models/{model_id}/
- HuggingFace Hub: Uses huggingface_hub.snapshot_download() with native cache
- NGC: NVIDIA NGC registry (stub, not yet implemented)

Configure via DALSTON_MODEL_SOURCE environment variable:
- "s3"   - S3 only (default, requires DALSTON_S3_BUCKET)
- "hf"   - HuggingFace Hub only (requires HF_TOKEN for gated models)
- "ngc"  - NGC only (requires NGC_API_KEY) — not yet implemented
- "auto" - Try local cache → S3 → HF → NGC, use first that works

S3 structure:
    s3://{bucket}/models/{model_id}/
        model.bin
        config.json
        ...
        .complete  # Marker file indicating upload is complete

Local cache structure (S3 backend):
    {cache_dir}/{model_id}/
        model.bin
        config.json
        ...
        .complete  # Marker file indicating download is complete

HuggingFace cache structure (HF backend):
    Uses native huggingface_hub cache at $HF_HOME/hub with
    content-addressed blob storage and symlink deduplication.
"""

from __future__ import annotations

import enum
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from dalston.engine_sdk.io import get_s3_client
from dalston.engine_sdk.model_paths import MODEL_BASE, is_model_cached

if TYPE_CHECKING:
    from dalston.engine_sdk.disk_cache import DiskCacheEvictor

logger = structlog.get_logger()


class ModelSource(enum.StrEnum):
    """Model download source configuration."""

    S3 = "s3"
    HF = "hf"
    NGC = "ngc"
    AUTO = "auto"


class ModelNotFoundError(Exception):
    """Raised when a model cannot be found in any configured source."""

    def __init__(self, model_id: str, sources_tried: list[str]) -> None:
        self.model_id = model_id
        self.sources_tried = sources_tried
        super().__init__(
            f"Model {model_id} not found in any source. Tried: {', '.join(sources_tried)}"
        )


# Marker file indicating a complete model download/upload
COMPLETE_MARKER = ".complete"

# Marker file recording last access time for disk cache eviction
ACCESS_MARKER = ".last_accessed"


def _touch_access_marker(path: Path) -> None:
    """Record last access time for disk cache eviction."""
    try:
        marker = path / ACCESS_MARKER
        marker.write_text(str(time.time()))
    except OSError:
        pass


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
            _touch_access_marker(local_path)
            return local_path

        # Download from S3
        logger.info("model_cache_miss", model_id=model_id, downloading_from="s3")
        self._download_from_s3(model_id, local_path)
        _touch_access_marker(local_path)

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
                if not relative or relative == COMPLETE_MARKER:
                    continue
                # Reject path traversal attempts
                if ".." in relative or relative.startswith("/"):
                    logger.warning(
                        "s3_path_traversal_blocked",
                        model_id=model_id,
                        key=key,
                        relative=relative,
                    )
                    continue
                files_to_download.append((key, relative))

        if not files_to_download:
            raise ModelNotInS3Error(model_id, self.bucket)

        # Download to temp directory on the same filesystem as the target
        # (avoids /tmp tmpfs size limits and enables atomic rename)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix="dalston-model-", dir=str(local_path.parent))
        )
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


class HFModelStorage:
    """Downloads models from HuggingFace Hub using native cache layout.

    Uses huggingface_hub.snapshot_download() which handles:
    - Content-addressed blob storage with symlink deduplication
    - Resumable downloads
    - Partial download recovery

    The native HF cache is used as-is — no .complete markers or custom
    cache structure. Framework-specific managers already know how to load
    from HF cache paths via model_paths.get_hf_model_path().
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("HF_TOKEN")

    def ensure_local(self, model_id: str) -> Path:
        """Download model from HuggingFace Hub if not cached locally.

        Args:
            model_id: HuggingFace model ID (e.g., "Systran/faster-whisper-large-v3")

        Returns:
            Path to the downloaded model snapshot directory
        """
        from huggingface_hub import snapshot_download

        logger.info("ensuring_model_from_hf", model_id=model_id)
        local_path = Path(
            snapshot_download(
                model_id,
                token=self.token,
                ignore_patterns=[
                    "*.py",
                    "*.pkl",
                    "*.pickle",
                    "*.sh",
                    "*.bat",
                    "*.exe",
                    "*.so",
                    "*.dll",
                ],
            )
        )
        logger.info(
            "model_ready_from_hf",
            model_id=model_id,
            local_path=str(local_path),
        )
        # Touch access marker on the top-level model dir (not the snapshot)
        # e.g., models--Systran--faster-whisper-base/ (parent of snapshots/)
        model_dir = local_path.parent.parent  # snapshot → snapshots → model dir
        _touch_access_marker(model_dir)
        return local_path

    def is_cached_locally(self, model_id: str) -> bool:
        """Check if model is in the local HuggingFace cache."""
        return is_model_cached(model_id, framework="huggingface")

    def get_cache_stats(self) -> dict:
        """Get HF cache statistics (basic)."""
        return {
            "source": "hf",
            "models": [],
            "total_size_mb": 0,
            "model_count": 0,
        }


class NGCModelStorage:
    """Downloads models from NVIDIA NGC registry.

    Requires NGC_API_KEY environment variable.
    Uses NeMo cache layout at model_paths.NEMO_CACHE.

    NOTE: This is a stub. Full NGC download support will be implemented
    when NeMo engine work requires it.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("NGC_API_KEY")

    def ensure_local(self, model_id: str) -> Path:
        """Download model from NGC.

        Not yet implemented — raises NotImplementedError with guidance.
        """
        raise NotImplementedError(
            f"NGC model download not yet implemented for '{model_id}'. "
            "NeMo models currently download via from_pretrained() which "
            "handles NGC downloads internally. Set DALSTON_MODEL_SOURCE=auto "
            "to fall back to HuggingFace Hub."
        )

    def is_cached_locally(self, model_id: str) -> bool:
        """Check if model is in the local NeMo cache."""
        return is_model_cached(model_id, framework="nemo")

    def get_cache_stats(self) -> dict:
        """Get NGC cache statistics (basic)."""
        return {
            "source": "ngc",
            "models": [],
            "total_size_mb": 0,
            "model_count": 0,
        }


class MultiSourceModelStorage:
    """Tries model download backends in order based on DALSTON_MODEL_SOURCE.

    Resolution order for 'auto':
      1. Local cache (any backend) → use immediately
      2. S3 (if DALSTON_S3_BUCKET set) → download + cache
      3. HF Hub (if model_id looks like HF repo) → snapshot_download
      4. NGC (if NGC_API_KEY set) → not yet implemented
      5. Raise ModelNotFoundError
    """

    def __init__(
        self,
        source: ModelSource,
        s3: S3ModelStorage | None = None,
        hf: HFModelStorage | None = None,
        ngc: NGCModelStorage | None = None,
    ) -> None:
        self.source = source
        self._s3 = s3
        self._hf = hf
        self._ngc = ngc
        self._disk_evictor: DiskCacheEvictor | None = None

        logger.info(
            "multi_source_storage_init",
            source=source.value,
            s3_enabled=s3 is not None,
            hf_enabled=hf is not None,
            ngc_enabled=ngc is not None,
        )

    @classmethod
    def from_env(cls) -> MultiSourceModelStorage:
        """Create storage from environment variables.

        Environment variables:
            DALSTON_MODEL_SOURCE: Source mode ("s3", "hf", "ngc", "auto")
            DALSTON_S3_BUCKET: S3 bucket (enables S3 backend)
            HF_TOKEN: HuggingFace token (for gated models)
            NGC_API_KEY: NGC API key (enables NGC backend)
        """
        source_str = os.environ.get("DALSTON_MODEL_SOURCE", "s3").lower()
        try:
            source = ModelSource(source_str)
        except ValueError:
            logger.warning(
                "invalid_model_source_defaulting_to_s3",
                configured=source_str,
                valid=list(ModelSource),
            )
            source = ModelSource.S3

        # Build available backends
        s3 = None
        s3_bucket = os.environ.get("DALSTON_S3_BUCKET")
        if s3_bucket:
            s3 = S3ModelStorage.from_env()

        hf = HFModelStorage()

        ngc = None
        if os.environ.get("NGC_API_KEY"):
            ngc = NGCModelStorage()

        return cls(source=source, s3=s3, hf=hf, ngc=ngc)

    def set_disk_evictor(self, evictor: DiskCacheEvictor) -> None:
        """Attach a disk cache evictor for post-download eviction."""
        self._disk_evictor = evictor

    def ensure_local(self, model_id: str) -> Path:
        """Ensure model is available locally from the configured source.

        Args:
            model_id: Model identifier (e.g., "Systran/faster-whisper-large-v3")

        Returns:
            Path to local model directory

        Raises:
            ModelNotFoundError: If model cannot be found in any source
            ModelNotInS3Error: If source is S3-only and model is not in S3
        """
        was_cached = self.is_cached_locally(model_id)

        if self.source == ModelSource.S3:
            result = self._ensure_from_s3(model_id)
        elif self.source == ModelSource.HF:
            result = self._ensure_from_hf(model_id)
        elif self.source == ModelSource.NGC:
            result = self._ensure_from_ngc(model_id)
        else:
            result = self._ensure_auto(model_id)

        # Trigger eviction after a fresh download (not cache hits).
        # Run on a background thread to avoid blocking the caller — this
        # code path runs under ModelManager._lock during _load_model().
        if not was_cached and self._disk_evictor is not None:
            import threading

            def _evict() -> None:
                try:
                    self._disk_evictor.scan_and_evict()  # type: ignore[union-attr]
                except Exception:
                    logger.exception("post_download_eviction_error")

            threading.Thread(
                target=_evict, daemon=True, name="post-download-eviction"
            ).start()

        return result

    def _ensure_from_s3(self, model_id: str) -> Path:
        """Download from S3 only."""
        if self._s3 is None:
            raise ValueError("DALSTON_MODEL_SOURCE=s3 but DALSTON_S3_BUCKET is not set")
        return self._s3.ensure_local(model_id)

    def _ensure_from_hf(self, model_id: str) -> Path:
        """Download from HuggingFace Hub only."""
        if self._hf is None:
            raise ValueError("HuggingFace storage not available")
        return self._hf.ensure_local(model_id)

    def _ensure_from_ngc(self, model_id: str) -> Path:
        """Download from NGC only."""
        if self._ngc is None:
            raise ValueError("DALSTON_MODEL_SOURCE=ngc but NGC_API_KEY is not set")
        return self._ngc.ensure_local(model_id)

    def _ensure_auto(self, model_id: str) -> Path:
        """Try sources in order: S3 → HF → NGC."""
        sources_tried: list[str] = []

        # Try S3 first (if configured)
        if self._s3 is not None:
            try:
                return self._s3.ensure_local(model_id)
            except Exception as exc:
                sources_tried.append("s3")
                logger.debug(
                    "auto_source_s3_failed",
                    model_id=model_id,
                    error=str(exc),
                )

        # Try HuggingFace Hub
        if self._hf is not None:
            try:
                return self._hf.ensure_local(model_id)
            except Exception as exc:
                sources_tried.append("hf")
                logger.debug(
                    "auto_source_hf_failed",
                    model_id=model_id,
                    error=str(exc),
                )

        # Try NGC (if configured)
        if self._ngc is not None:
            try:
                return self._ngc.ensure_local(model_id)
            except Exception as exc:
                sources_tried.append("ngc")
                logger.debug(
                    "auto_source_ngc_failed",
                    model_id=model_id,
                    error=str(exc),
                )

        raise ModelNotFoundError(model_id, sources_tried)

    def is_cached_locally(self, model_id: str) -> bool:
        """Check if model is in any local cache."""
        if self._s3 is not None and self._s3.is_cached_locally(model_id):
            return True
        if self._hf is not None and self._hf.is_cached_locally(model_id):
            return True
        if self._ngc is not None and self._ngc.is_cached_locally(model_id):
            return True
        return False

    def get_cache_stats(self) -> dict:
        """Get cache statistics from the primary backend."""
        if self.source == ModelSource.S3 and self._s3 is not None:
            return self._s3.get_cache_stats()
        if self.source == ModelSource.HF and self._hf is not None:
            return self._hf.get_cache_stats()
        if self.source == ModelSource.NGC and self._ngc is not None:
            return self._ngc.get_cache_stats()
        # Auto mode: prefer S3 stats if available
        if self._s3 is not None:
            return self._s3.get_cache_stats()
        if self._hf is not None:
            return self._hf.get_cache_stats()
        return {"source": "none", "models": [], "total_size_mb": 0, "model_count": 0}
