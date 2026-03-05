"""Model registry service for managing model downloads and metadata.

This service provides CRUD operations on the models table and handles
model downloads from HuggingFace Hub. Models are stored in S3 as the
canonical source; engines pull from S3 to local cache.

Status flow:
    not_downloaded → downloading → ready
                         ↓
                      failed

S3 structure:
    s3://{bucket}/models/{model_id}/
        model.bin, config.json, etc.
        .complete  # Marker indicating upload is complete
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.model_selection_keys import ACTIVE_MODEL_SELECTOR_KEYS
from dalston.common.s3 import get_s3_client
from dalston.config import get_settings
from dalston.db.models import JobModel, ModelRegistryModel
from dalston.gateway.dependencies import get_audit_service

# Marker file indicating a complete model upload
COMPLETE_MARKER = ".complete"

# S3 prefix for models
MODELS_PREFIX = "models"

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger()


class ModelNotFoundError(Exception):
    """Raised when a requested model doesn't exist in the registry."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Model not found: {model_id}")


class ModelNotDownloadedError(Exception):
    """Raised when trying to use a model that isn't downloaded."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(
            f"Model {model_id} not downloaded. Run: dalston model pull {model_id}"
        )


class ModelInUseError(Exception):
    """Raised when trying to delete a model that has pending jobs."""

    def __init__(self, model_id: str, job_count: int) -> None:
        self.model_id = model_id
        self.job_count = job_count
        super().__init__(
            f"Cannot delete model {model_id}: {job_count} pending/processing job(s) using it"
        )


class ModelRegistryService:
    """Service for model download and registry management.

    Handles:
    - CRUD operations on model registry entries
    - Model downloads from HuggingFace Hub
    - Synchronization between registry and disk state
    - Usage tracking (last_used_at)
    """

    async def get_model(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> ModelRegistryModel | None:
        """Get a model by ID.

        Args:
            db: Database session
            model_id: Dalston model ID (e.g., "parakeet-tdt-1.1b")

        Returns:
            Model if found, None otherwise
        """
        result = await db.execute(
            select(ModelRegistryModel).where(ModelRegistryModel.id == model_id)
        )
        return result.scalar_one_or_none()

    async def get_model_or_raise(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> ModelRegistryModel:
        """Get a model by ID, raising if not found.

        Args:
            db: Database session
            model_id: Dalston model ID

        Returns:
            Model entry

        Raises:
            ModelNotFoundError: If model doesn't exist in registry
        """
        model = await self.get_model(db, model_id)
        if model is None:
            raise ModelNotFoundError(model_id)
        return model

    async def get_model_by_runtime_model_id(
        self,
        db: AsyncSession,
        runtime_model_id: str,
    ) -> ModelRegistryModel | None:
        """Get a model by its runtime_model_id.

        This is used to check if a HuggingFace model is already registered
        under a different Dalston model ID.

        Args:
            db: Database session
            runtime_model_id: The HuggingFace model ID or runtime-specific model ID

        Returns:
            Model if found, None otherwise
        """
        result = await db.execute(
            select(ModelRegistryModel).where(
                ModelRegistryModel.runtime_model_id == runtime_model_id
            )
        )
        return result.scalar_one_or_none()

    async def list_models(
        self,
        db: AsyncSession,
        *,
        stage: str | None = None,
        runtime: str | None = None,
        status: str | None = None,
    ) -> Sequence[ModelRegistryModel]:
        """List models with optional filters.

        Args:
            db: Database session
            stage: Filter by stage (transcribe, diarize, align, etc.)
            runtime: Filter by runtime (faster-whisper, nemo, etc.)
            status: Filter by status (not_downloaded, downloading, ready, failed)

        Returns:
            List of matching models, ordered by ID
        """
        query = select(ModelRegistryModel)

        if stage is not None:
            query = query.where(ModelRegistryModel.stage == stage)
        if runtime is not None:
            query = query.where(ModelRegistryModel.runtime == runtime)
        if status is not None:
            query = query.where(ModelRegistryModel.status == status)

        query = query.order_by(ModelRegistryModel.id)
        result = await db.execute(query)
        return result.scalars().all()

    async def set_model_status(
        self,
        db: AsyncSession,
        model_id: str,
        status: str,
    ) -> None:
        """Update a model's status.

        Args:
            db: Database session
            model_id: Model ID
            status: New status (not_downloaded, downloading, ready, failed)
        """
        await db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(status=status)
        )
        await db.commit()

    async def pull_model(
        self,
        db: AsyncSession,
        model_id: str,
        *,
        force: bool = False,
    ) -> ModelRegistryModel:
        """Download a model from HuggingFace Hub and upload to S3.

        This is a potentially long-running operation. For async usage,
        consider calling this in a background task.

        Flow:
            1. Download from HuggingFace to temp directory
            2. Upload all files to S3
            3. Update registry with S3 URI

        Args:
            db: Database session
            model_id: Dalston model ID
            force: Re-download even if already present

        Returns:
            Updated model entry

        Raises:
            ModelNotFoundError: If model doesn't exist in registry
        """
        model = await self.get_model_or_raise(db, model_id)
        settings = get_settings()

        if model.status == "ready" and not force:
            logger.info("model_already_in_s3", model_id=model_id)
            return model

        # Status should already be "downloading" (set by endpoint before background task)
        # but set it here too for direct calls to this method
        if model.status != "downloading":
            await db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(status="downloading")
            )
            await db.commit()
            # Refresh model to get updated status
            await db.refresh(model)

        try:
            # Use source (HuggingFace repo ID) for download, not runtime_model_id
            # runtime_model_id is what the engine uses internally (e.g., "base")
            # source is the HuggingFace repo (e.g., "Systran/faster-whisper-base")
            hf_repo_id = model.source or model.runtime_model_id
            logger.info(
                "downloading_model_from_hf",
                model_id=model_id,
                hf_repo_id=hf_repo_id,
            )

            # Import here to avoid dependency on huggingface_hub at import time
            from huggingface_hub import snapshot_download

            # Download from HuggingFace to temp directory
            temp_dir = Path(tempfile.mkdtemp(prefix="dalston-model-"))
            try:
                downloaded_path_str = await asyncio.to_thread(
                    snapshot_download,
                    hf_repo_id,
                    local_dir=str(temp_dir),
                    force_download=force,
                )
                downloaded_path = Path(downloaded_path_str)

                # Calculate size
                size_bytes = sum(
                    f.stat().st_size for f in downloaded_path.rglob("*") if f.is_file()
                )

                logger.info(
                    "uploading_model_to_s3",
                    model_id=model_id,
                    size_mb=round(size_bytes / 1024 / 1024, 1),
                )

                # Upload to S3
                s3_uri = await self._upload_model_to_s3(
                    local_path=downloaded_path,
                    model_id=model_id,
                    bucket=settings.s3_bucket,
                )

            finally:
                # Clean up temp directory
                shutil.rmtree(temp_dir, ignore_errors=True)

            # Update registry with success
            now = datetime.now(UTC)
            await db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(
                    status="ready",
                    download_path=s3_uri,
                    size_bytes=size_bytes,
                    downloaded_at=now,
                )
            )
            await db.commit()

            logger.info(
                "model_uploaded_to_s3",
                model_id=model_id,
                size_mb=round(size_bytes / 1024 / 1024, 1),
                s3_uri=s3_uri,
            )

            # Audit log
            audit = get_audit_service()
            await audit.log_model_downloaded(
                model_id=model_id,
                source=hf_repo_id,
                size_bytes=size_bytes,
                download_path=s3_uri,
            )

        except Exception as e:
            # Update status to failed
            logger.exception("model_download_failed", model_id=model_id)
            await db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(
                    status="failed",
                    model_metadata={"error": str(e)},
                )
            )
            await db.commit()

            # Audit log
            audit = get_audit_service()
            await audit.log_model_download_failed(
                model_id=model_id,
                error=str(e),
            )

            raise

        # Refresh and return
        await db.refresh(model)
        return model

    async def _upload_model_to_s3(
        self,
        local_path: Path,
        model_id: str,
        bucket: str,
    ) -> str:
        """Upload a model directory to S3.

        Args:
            local_path: Local directory containing model files
            model_id: Model identifier (used as S3 key prefix)
            bucket: S3 bucket name

        Returns:
            S3 URI of the uploaded model (s3://bucket/models/model_id/)
        """
        s3_prefix = f"{MODELS_PREFIX}/{model_id}/"

        async with get_s3_client() as s3:
            # Upload all files
            for file_path in local_path.rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(local_path)
                    s3_key = f"{s3_prefix}{relative}"

                    # Read file and upload
                    content = await asyncio.to_thread(file_path.read_bytes)
                    await s3.put_object(
                        Bucket=bucket,
                        Key=s3_key,
                        Body=content,
                    )

            # Upload .complete marker
            await s3.put_object(
                Bucket=bucket,
                Key=f"{s3_prefix}{COMPLETE_MARKER}",
                Body=b"",
            )

        return f"s3://{bucket}/{s3_prefix}"

    async def _is_model_in_s3(self, model_id: str, bucket: str) -> bool:
        """Check if a model exists in S3 (has .complete marker)."""
        s3_key = f"{MODELS_PREFIX}/{model_id}/{COMPLETE_MARKER}"

        async with get_s3_client() as s3:
            try:
                await s3.head_object(Bucket=bucket, Key=s3_key)
                return True
            except Exception:
                return False

    async def _get_model_size_in_s3(self, model_id: str, bucket: str) -> int | None:
        """Get total size of a model in S3."""
        s3_prefix = f"{MODELS_PREFIX}/{model_id}/"

        async with get_s3_client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            total_size = 0

            async for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix):
                for obj in page.get("Contents", []):
                    if not obj["Key"].endswith(COMPLETE_MARKER):
                        total_size += obj["Size"]

            return total_size if total_size > 0 else None

    async def _delete_model_from_s3(self, model_id: str, bucket: str) -> None:
        """Delete all files for a model from S3."""
        s3_prefix = f"{MODELS_PREFIX}/{model_id}/"

        async with get_s3_client() as s3:
            # List all objects with this prefix
            paginator = s3.get_paginator("list_objects_v2")
            objects_to_delete: list[dict] = []

            async for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix):
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})

            # Delete in batches of 1000 (S3 limit)
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                await s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch},
                )

            logger.info(
                "model_deleted_from_s3",
                model_id=model_id,
                objects_deleted=len(objects_to_delete),
            )

    async def _check_model_in_use(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> int:
        """Check if a model is being used by pending or processing jobs.

        Args:
            db: Database session
            model_id: Dalston model ID

        Returns:
            Number of pending/processing jobs using this model
        """
        # Query jobs where status is pending/processing and model matches
        # any stage-level model selector parameter.
        from sqlalchemy import func, or_

        model_filters = [
            JobModel.parameters[key].astext == model_id
            for key in ACTIVE_MODEL_SELECTOR_KEYS
        ]

        result = await db.execute(
            select(func.count())
            .select_from(JobModel)
            .where(
                JobModel.status.in_(["pending", "processing"]),
                or_(*model_filters),
            )
        )
        return result.scalar() or 0

    async def remove_model(
        self,
        db: AsyncSession,
        model_id: str,
        *,
        purge: bool = False,
    ) -> None:
        """Remove a downloaded model from disk and optionally from registry.

        Args:
            db: Database session
            model_id: Dalston model ID
            purge: If True, delete the model from registry entirely.
                   If False (default), only remove files and reset status.

        Raises:
            ModelNotFoundError: If model doesn't exist in registry
            ModelInUseError: If model has pending/processing jobs
        """
        model = await self.get_model_or_raise(db, model_id)

        # Check if model is in use by pending jobs
        job_count = await self._check_model_in_use(db, model_id)
        if job_count > 0:
            raise ModelInUseError(model_id, job_count)

        # Remove files from S3 if they exist
        settings = get_settings()
        download_path = model.download_path
        if download_path and download_path.startswith("s3://"):
            await self._delete_model_from_s3(model_id, settings.s3_bucket)
            logger.info(
                "model_files_removed_from_s3",
                model_id=model_id,
                s3_uri=download_path,
            )

        audit = get_audit_service()

        if purge:
            # Delete from registry entirely
            await db.execute(
                delete(ModelRegistryModel).where(ModelRegistryModel.id == model_id)
            )
            await db.commit()
            logger.info("model_deleted_from_registry", model_id=model_id)
            await audit.log_model_deleted_from_registry(
                model_id=model_id,
                download_path=str(download_path) if download_path else None,
            )
        else:
            # Update registry status (keep entry)
            await db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(
                    status="not_downloaded",
                    download_path=None,
                    size_bytes=None,
                    downloaded_at=None,
                )
            )
            await db.commit()
            logger.info("model_removed", model_id=model_id)
            await audit.log_model_removed(
                model_id=model_id,
                download_path=str(download_path) if download_path else None,
            )

    async def sync_from_s3(
        self,
        db: AsyncSession,
    ) -> dict[str, int]:
        """Sync registry status with S3 state.

        Checks each model against S3 to determine if files are present.
        Models in S3 (with .complete marker) are marked "ready".
        Models missing from S3 are marked "not_downloaded".

        Returns:
            Dict with counts: {"updated": N, "unchanged": N}
        """
        settings = get_settings()
        models = await self.list_models(db)
        synced = {"updated": 0, "unchanged": 0}

        for model in models:
            in_s3 = await self._is_model_in_s3(model.id, settings.s3_bucket)

            if in_s3 and model.status != "ready":
                # Model is in S3 but registry says not downloaded
                size_bytes = await self._get_model_size_in_s3(
                    model.id, settings.s3_bucket
                )
                s3_uri = f"s3://{settings.s3_bucket}/{MODELS_PREFIX}/{model.id}/"

                await db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(
                        status="ready",
                        download_path=s3_uri,
                        size_bytes=size_bytes,
                    )
                )
                synced["updated"] += 1
                logger.info(
                    "model_synced_to_ready",
                    model_id=model.id,
                    s3_uri=s3_uri,
                )

            elif not in_s3 and model.status == "ready":
                # Registry says ready but files are missing from S3
                await db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(
                        status="not_downloaded",
                        download_path=None,
                        size_bytes=None,
                    )
                )
                synced["updated"] += 1
                logger.info("model_synced_to_not_downloaded", model_id=model.id)

            else:
                synced["unchanged"] += 1

        await db.commit()
        return synced

    # Keep old name as alias for backward compatibility
    sync_from_disk = sync_from_s3

    async def touch_model(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> None:
        """Update last_used_at timestamp for a model.

        Called by engines when they load a model to track usage.

        Args:
            db: Database session
            model_id: Dalston model ID
        """
        now = datetime.now(UTC)
        await db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(last_used_at=now)
        )
        await db.commit()

    async def register_model(
        self,
        db: AsyncSession,
        *,
        model_id: str,
        runtime: str,
        runtime_model_id: str,
        stage: str,
        name: str | None = None,
        source: str | None = None,
        library_name: str | None = None,
        languages: list[str] | None = None,
        word_timestamps: bool = False,
        punctuation: bool = False,
        capitalization: bool = False,
        streaming: bool = False,
        min_vram_gb: float | None = None,
        min_ram_gb: float | None = None,
        supports_cpu: bool = True,
        model_metadata: dict | None = None,
    ) -> ModelRegistryModel:
        """Register a new model in the registry.

        Args:
            db: Database session
            model_id: Dalston model ID
            runtime: Engine runtime (faster-whisper, nemo, etc.)
            runtime_model_id: HuggingFace model ID or local path
            stage: Pipeline stage (transcribe, diarize, etc.)
            name: Human-readable name
            source: Model source (huggingface, local)
            library_name: ML library (ctranslate2, nemo, etc.)
            languages: Supported language codes
            word_timestamps: Whether model provides word timestamps
            punctuation: Whether model provides punctuation
            capitalization: Whether model provides proper capitalization
            streaming: Whether model supports streaming
            min_vram_gb: Minimum GPU VRAM required
            min_ram_gb: Minimum RAM required
            supports_cpu: Whether model can run on CPU

        Returns:
            Created model entry
        """
        model = ModelRegistryModel(
            id=model_id,
            name=name,
            runtime=runtime,
            runtime_model_id=runtime_model_id,
            stage=stage,
            status="not_downloaded",
            source=source,
            library_name=library_name,
            languages=languages,
            word_timestamps=word_timestamps,
            punctuation=punctuation,
            capitalization=capitalization,
            streaming=streaming,
            min_vram_gb=min_vram_gb,
            min_ram_gb=min_ram_gb,
            supports_cpu=supports_cpu,
            model_metadata=model_metadata or {},
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)

        logger.info(
            "model_registered",
            model_id=model_id,
            runtime=runtime,
            stage=stage,
        )

        return model

    async def ensure_ready(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> ModelRegistryModel:
        """Ensure a model is downloaded and ready for use.

        Args:
            db: Database session
            model_id: Dalston model ID

        Returns:
            Model entry (with status="ready")

        Raises:
            ModelNotFoundError: If model doesn't exist
            ModelNotDownloadedError: If model is not downloaded
        """
        model = await self.get_model_or_raise(db, model_id)

        if model.status != "ready":
            raise ModelNotDownloadedError(model_id)

        return model

    async def seed_from_yamls(
        self,
        db: AsyncSession,
        *,
        models_dir: Path | None = None,
    ) -> dict[str, int]:
        """Seed registry from YAML files, preserving user-modified entries.

        This replaces the manual seed_from_catalog() flow with direct YAML loading.
        Called automatically on gateway startup.

        For each YAML model:
        - If not in DB: INSERT with metadata_source="yaml"
        - If in DB with metadata_source="yaml": UPDATE all fields
        - If in DB with metadata_source="user": SKIP (preserve user edits)
        - If in DB with metadata_source="hf": UPDATE (improve HF-resolved data)

        Args:
            db: Database session
            models_dir: Directory containing model YAMLs. Defaults to repo/models/

        Returns:
            Dict with counts: {"created": N, "updated": N, "preserved": N}
        """
        from dalston.gateway.services.model_yaml_loader import load_model_yamls

        entries = load_model_yamls(models_dir)
        result = {"created": 0, "updated": 0, "preserved": 0}

        for entry in entries:
            existing = await self.get_model(db, entry.id)

            if existing is None:
                # New model - insert
                model = ModelRegistryModel(
                    id=entry.id,
                    name=entry.name,
                    runtime=entry.runtime,
                    runtime_model_id=entry.runtime_model_id,
                    stage=entry.stage,
                    status="not_downloaded",
                    source=entry.source,
                    languages=entry.languages,
                    word_timestamps=entry.word_timestamps,
                    punctuation=entry.punctuation,
                    capitalization=entry.capitalization,
                    streaming=entry.streaming,
                    min_vram_gb=entry.min_vram_gb,
                    min_ram_gb=entry.min_ram_gb,
                    supports_cpu=entry.supports_cpu,
                    metadata_source="yaml",
                )
                db.add(model)
                result["created"] += 1

            elif existing.metadata_source == "user":
                # User-modified - preserve
                result["preserved"] += 1

            else:
                # yaml or hf - update with fresh YAML data
                await db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == entry.id)
                    .values(
                        name=entry.name,
                        runtime=entry.runtime,
                        runtime_model_id=entry.runtime_model_id,
                        stage=entry.stage,
                        source=entry.source,
                        languages=entry.languages,
                        word_timestamps=entry.word_timestamps,
                        punctuation=entry.punctuation,
                        capitalization=entry.capitalization,
                        streaming=entry.streaming,
                        min_vram_gb=entry.min_vram_gb,
                        min_ram_gb=entry.min_ram_gb,
                        supports_cpu=entry.supports_cpu,
                        metadata_source="yaml",
                    )
                )
                result["updated"] += 1

        await db.commit()

        logger.info(
            "model_yamls_seeded",
            created=result["created"],
            updated=result["updated"],
            preserved=result["preserved"],
        )

        return result

    async def update_model(
        self,
        db: AsyncSession,
        model_id: str,
        updates: dict,
    ) -> ModelRegistryModel:
        """Update model metadata and set metadata_source to 'user'.

        This marks the model as user-modified, preventing automatic overwrites
        during re-seeding from YAML files.

        Args:
            db: Database session
            model_id: Dalston model ID
            updates: Dictionary of fields to update

        Returns:
            Updated model entry

        Raises:
            ModelNotFoundError: If model doesn't exist
        """
        model = await self.get_model_or_raise(db, model_id)

        # Update allowed fields
        allowed_fields = {
            "name",
            "languages",
            "word_timestamps",
            "punctuation",
            "capitalization",
            "streaming",
            "min_vram_gb",
            "min_ram_gb",
            "supports_cpu",
        }

        for key, value in updates.items():
            if key in allowed_fields and hasattr(model, key):
                setattr(model, key, value)

        # Mark as user-modified
        model.metadata_source = "user"

        await db.commit()
        await db.refresh(model)

        logger.info(
            "model_updated",
            model_id=model_id,
            fields=list(updates.keys()),
        )

        return model

    async def resolve_hf_model(
        self,
        db: AsyncSession,
        hf_model_id: str,
        *,
        auto_register: bool = True,
    ) -> ModelRegistryModel | None:
        """Resolve a HuggingFace model ID and optionally auto-register it.

        This method enables dynamic model support by:
        1. Checking if the model already exists in the registry
        2. If not, fetching metadata from HuggingFace Hub
        3. Determining the appropriate runtime based on library_name/tags
        4. Optionally auto-registering the model in the database

        Args:
            db: Database session
            hf_model_id: HuggingFace model ID (e.g., "nvidia/parakeet-tdt-1.1b")
            auto_register: If True, automatically register the model if resolved

        Returns:
            ModelRegistryModel if model exists or was successfully resolved,
            None if the model couldn't be resolved.
        """
        # First check if model already exists
        existing = await self.get_model(db, hf_model_id)
        if existing is not None:
            return existing

        # Only try HF resolution for model IDs that look like HF format (org/model)
        if "/" not in hf_model_id:
            logger.debug(
                "skipping_hf_resolution_not_hf_format",
                model_id=hf_model_id,
            )
            return None

        # Import here to avoid circular dependency
        from dalston.gateway.services.hf_resolver import HFResolver

        resolver = HFResolver()
        metadata = await resolver.get_model_metadata(hf_model_id)

        if metadata is None:
            logger.warning(
                "hf_model_not_found",
                model_id=hf_model_id,
            )
            return None

        if metadata.resolved_runtime is None:
            logger.warning(
                "hf_model_runtime_not_resolved",
                model_id=hf_model_id,
                library_name=metadata.library_name,
                pipeline_tag=metadata.pipeline_tag,
            )
            return None

        logger.info(
            "hf_model_resolved",
            model_id=hf_model_id,
            runtime=metadata.resolved_runtime,
            library_name=metadata.library_name,
        )

        if not auto_register:
            # Return a transient model object (not persisted)
            return ModelRegistryModel(
                id=hf_model_id,
                runtime=metadata.resolved_runtime,
                runtime_model_id=hf_model_id,
                stage="transcribe",
                status="not_downloaded",
                source=hf_model_id,
                library_name=metadata.library_name,
                languages=metadata.languages if metadata.languages else None,
            )

        # Auto-register the model
        return await self.register_model(
            db,
            model_id=hf_model_id,
            runtime=metadata.resolved_runtime,
            runtime_model_id=hf_model_id,
            stage="transcribe",
            source=hf_model_id,
            library_name=metadata.library_name,
            languages=metadata.languages if metadata.languages else None,
            model_metadata={
                "pipeline_tag": metadata.pipeline_tag,
                "tags": metadata.tags[:50],  # Limit stored tags
                "downloads": metadata.downloads,
                "likes": metadata.likes,
                "auto_registered": True,
            },
        )

    async def get_or_resolve_model(
        self,
        db: AsyncSession,
        model_id: str,
        *,
        auto_register: bool = True,
    ) -> ModelRegistryModel | None:
        """Get a model by ID, resolving from HuggingFace if needed.

        This is the primary method for model lookup that supports both:
        - Dalston model IDs (from catalog/registry)
        - HuggingFace model IDs (auto-resolved and optionally registered)

        Args:
            db: Database session
            model_id: Dalston model ID or HuggingFace model ID
            auto_register: If True, auto-register HF models

        Returns:
            ModelRegistryModel if found/resolved, None otherwise
        """
        # Try direct lookup first
        model = await self.get_model(db, model_id)
        if model is not None:
            return model

        # Try HuggingFace resolution
        return await self.resolve_hf_model(
            db,
            model_id,
            auto_register=auto_register,
        )
