"""Model registry service for managing model downloads and metadata.

This service provides CRUD operations on the models table and handles
model downloads from HuggingFace Hub. It integrates with the unified
model cache from M39.

Status flow:
    not_downloaded → downloading → ready
                         ↓
                      failed
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import ModelRegistryModel
from dalston.engine_sdk.model_paths import HF_CACHE, get_hf_model_path, is_model_cached

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

    async def pull_model(
        self,
        db: AsyncSession,
        model_id: str,
        *,
        force: bool = False,
    ) -> ModelRegistryModel:
        """Download a model from HuggingFace Hub.

        This is a potentially long-running operation. For async usage,
        consider calling this in a background task.

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

        if model.status == "ready" and not force:
            logger.info("model_already_downloaded", model_id=model_id)
            return model

        # Update status to downloading
        await db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(status="downloading")
        )
        await db.commit()

        try:
            logger.info(
                "downloading_model",
                model_id=model_id,
                runtime_model_id=model.runtime_model_id,
            )

            # Import here to avoid dependency on huggingface_hub at import time
            from huggingface_hub import snapshot_download

            # Download from HuggingFace (blocking call wrapped in thread)
            local_path = await asyncio.to_thread(
                snapshot_download,
                model.runtime_model_id,
                cache_dir=str(HF_CACHE),
                force_download=force,
            )

            # Calculate size
            path = Path(local_path)
            size_bytes = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

            # Update registry with success
            now = datetime.now(UTC)
            await db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(
                    status="ready",
                    download_path=str(local_path),
                    size_bytes=size_bytes,
                    downloaded_at=now,
                )
            )
            await db.commit()

            logger.info(
                "model_downloaded",
                model_id=model_id,
                size_mb=round(size_bytes / 1024 / 1024, 1),
                path=str(local_path),
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
            raise

        # Refresh and return
        await db.refresh(model)
        return model

    async def remove_model(
        self,
        db: AsyncSession,
        model_id: str,
    ) -> None:
        """Remove a downloaded model from disk and update registry.

        Args:
            db: Database session
            model_id: Dalston model ID

        Raises:
            ModelNotFoundError: If model doesn't exist in registry
        """
        model = await self.get_model_or_raise(db, model_id)

        # Remove files if they exist
        if model.download_path:
            path = Path(model.download_path)
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
                logger.info(
                    "model_files_removed",
                    model_id=model_id,
                    path=str(path),
                )

        # Update registry
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

    async def sync_from_disk(
        self,
        db: AsyncSession,
    ) -> dict[str, int]:
        """Sync registry status with actual disk state.

        Checks each model's runtime_model_id against the HuggingFace cache
        to determine if files are present on disk.

        Returns:
            Dict with counts: {"updated": N, "unchanged": N}
        """
        models = await self.list_models(db)
        synced = {"updated": 0, "unchanged": 0}

        for model in models:
            on_disk = is_model_cached(model.runtime_model_id)

            if on_disk and model.status != "ready":
                # Model is on disk but registry says not downloaded
                download_path = get_hf_model_path(model.runtime_model_id)
                size_bytes = None
                if download_path.exists():
                    size_bytes = sum(
                        f.stat().st_size
                        for f in download_path.rglob("*")
                        if f.is_file()
                    )

                await db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(
                        status="ready",
                        download_path=str(download_path),
                        size_bytes=size_bytes,
                    )
                )
                synced["updated"] += 1
                logger.info(
                    "model_synced_to_ready",
                    model_id=model.id,
                    path=str(download_path),
                )

            elif not on_disk and model.status == "ready":
                # Registry says ready but files are missing
                await db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(
                        status="not_downloaded",
                        download_path=None,
                    )
                )
                synced["updated"] += 1
                logger.info("model_synced_to_not_downloaded", model_id=model.id)

            else:
                synced["unchanged"] += 1

        await db.commit()
        return synced

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

    async def seed_from_catalog(
        self,
        db: AsyncSession,
        *,
        update_existing: bool = False,
    ) -> dict[str, int]:
        """Seed the registry with models from the static catalog.

        This populates the database with all models defined in the catalog,
        allowing users to browse available models and download them.

        Args:
            db: Database session
            update_existing: If True, update existing models with catalog data

        Returns:
            Dict with counts: {"created": N, "updated": N, "skipped": N}
        """
        from dalston.orchestrator.catalog import get_catalog, reload_catalog

        # Force reload to get fresh data
        reload_catalog()
        catalog = get_catalog()
        models = catalog.get_all_models()

        created = 0
        updated = 0
        skipped = 0

        for m in models:
            # Check if model already exists
            existing = await self.get_model(db, m.id)
            if existing is not None:
                if update_existing:
                    # Update existing model with catalog data
                    await db.execute(
                        update(ModelRegistryModel)
                        .where(ModelRegistryModel.id == m.id)
                        .values(
                            name=m.name,
                            runtime=m.runtime,
                            runtime_model_id=m.runtime_model_id,
                            stage=m.stage or "transcribe",
                            source=m.source,
                            languages=m.languages,
                            word_timestamps=m.word_timestamps,
                            punctuation=m.punctuation,
                            capitalization=m.capitalization,
                            min_vram_gb=m.min_vram_gb,
                            min_ram_gb=m.min_ram_gb,
                            supports_cpu=m.supports_cpu,
                        )
                    )
                    updated += 1
                else:
                    skipped += 1
                continue

            # Create new registry entry
            model = ModelRegistryModel(
                id=m.id,
                name=m.name,
                runtime=m.runtime,
                runtime_model_id=m.runtime_model_id,
                stage=m.stage or "transcribe",
                status="not_downloaded",
                source=m.source,
                languages=m.languages,
                word_timestamps=m.word_timestamps,
                punctuation=m.punctuation,
                capitalization=m.capitalization,
                min_vram_gb=m.min_vram_gb,
                min_ram_gb=m.min_ram_gb,
                supports_cpu=m.supports_cpu,
            )
            db.add(model)
            created += 1

        await db.commit()

        logger.info(
            "catalog_seeded",
            created=created,
            updated=updated,
            skipped=skipped,
        )

        return {"created": created, "updated": updated, "skipped": skipped}
