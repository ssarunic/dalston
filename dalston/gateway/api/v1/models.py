"""Model catalog and registry API endpoints (M36, M40).

GET /v1/models - List all available model variants
GET /v1/models/{model_id} - Get details for a specific model
POST /v1/models/{model_id}/pull - Download a model from HuggingFace
DELETE /v1/models/{model_id} - Remove downloaded model files
POST /v1/models/sync - Sync registry with disk state

NOTE: This endpoint was repurposed in M36. Previously it listed running engines.
For running engine status, use GET /v1/engines instead.

M40 adds database-backed model registry with download management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.dependencies import get_db
from dalston.gateway.services.model_registry import (
    ModelNotFoundError,
    ModelRegistryService,
)
from dalston.orchestrator.catalog import get_catalog

router = APIRouter(prefix="/models", tags=["models"])

# Service singleton
_model_registry_service: ModelRegistryService | None = None


def get_model_registry_service() -> ModelRegistryService:
    """Get ModelRegistryService instance (singleton)."""
    global _model_registry_service
    if _model_registry_service is None:
        _model_registry_service = ModelRegistryService()
    return _model_registry_service


# Response models


class ModelCapabilitiesResponse(BaseModel):
    """Model capabilities in API response format."""

    word_timestamps: bool = False
    punctuation: bool = False
    capitalization: bool = False
    streaming: bool = False
    max_audio_duration_s: int | None = None


class ModelHardwareResponse(BaseModel):
    """Model hardware requirements in API response format."""

    min_vram_gb: int | None = None
    supports_cpu: bool = False
    min_ram_gb: int | None = None


class ModelPerformanceResponse(BaseModel):
    """Model performance characteristics in API response format."""

    rtf_gpu: float | None = None
    rtf_cpu: float | None = None


class ModelResponse(BaseModel):
    """Single model in API response."""

    id: str
    object: Literal["model"] = "model"
    name: str
    runtime: str
    runtime_model_id: str
    source: str | None = None
    size_gb: float | None = None
    stage: str | None = None
    languages: list[str] | None = None  # null means multilingual
    capabilities: ModelCapabilitiesResponse
    hardware: ModelHardwareResponse
    performance: ModelPerformanceResponse


class ModelListResponse(BaseModel):
    """Response for GET /v1/models."""

    object: Literal["list"] = "list"
    data: list[ModelResponse]


@router.get(
    "",
    response_model=ModelListResponse,
    summary="List model catalog",
    description=(
        "List all available model variants from the catalog. "
        "Each model maps to a runtime that can load it. "
        "Use the model ID with the `model` parameter in transcription requests."
    ),
)
async def list_models(
    runtime: str | None = Query(
        default=None,
        description="Filter by runtime (e.g., 'nemo', 'faster-whisper')",
    ),
    stage: str | None = Query(
        default=None,
        description="Filter by pipeline stage (e.g., 'transcribe')",
    ),
) -> ModelListResponse:
    """List all model variants from the catalog.

    Returns all models that can be used with the `model` parameter in
    transcription requests. Each model is served by a specific runtime.
    """
    catalog = get_catalog()

    # Get models with optional filtering
    if runtime:
        models = catalog.get_models_for_runtime(runtime)
    elif stage:
        models = catalog.get_models_for_stage(stage)
    else:
        models = catalog.get_all_models()

    data = [
        ModelResponse(
            id=m.id,
            name=m.name,
            runtime=m.runtime,
            runtime_model_id=m.runtime_model_id,
            source=m.source,
            size_gb=m.size_gb,
            stage=m.stage,
            languages=m.languages,
            capabilities=ModelCapabilitiesResponse(
                word_timestamps=m.word_timestamps,
                punctuation=m.punctuation,
                capitalization=m.capitalization,
                streaming=False,  # Batch models don't stream
            ),
            hardware=ModelHardwareResponse(
                min_vram_gb=m.min_vram_gb,
                supports_cpu=m.supports_cpu,
                min_ram_gb=m.min_ram_gb,
            ),
            performance=ModelPerformanceResponse(
                rtf_gpu=m.rtf_gpu,
                rtf_cpu=m.rtf_cpu,
            ),
        )
        for m in models
    ]

    return ModelListResponse(data=data)


# =============================================================================
# M40: Model Registry Endpoints
# =============================================================================


class ModelRegistryResponse(BaseModel):
    """Model registry entry with download status."""

    id: str
    object: Literal["model"] = "model"
    name: str | None = None
    runtime: str
    runtime_model_id: str
    stage: str
    status: str  # not_downloaded, downloading, ready, failed
    download_path: str | None = None
    size_bytes: int | None = None
    downloaded_at: datetime | None = None
    source: str | None = None
    library_name: str | None = None
    languages: list[str] | None = None
    word_timestamps: bool = False
    punctuation: bool = False
    capitalization: bool = False
    streaming: bool = False
    min_vram_gb: float | None = None
    min_ram_gb: float | None = None
    supports_cpu: bool = True
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ModelRegistryListResponse(BaseModel):
    """Response for listing registry models."""

    object: Literal["list"] = "list"
    data: list[ModelRegistryResponse]


class PullModelRequest(BaseModel):
    """Request body for pulling a model."""

    force: bool = False


class PullModelResponse(BaseModel):
    """Response for model pull operation."""

    message: str
    model_id: str
    status: str


class SyncModelsResponse(BaseModel):
    """Response for sync operation."""

    updated: int
    unchanged: int


class DeleteModelResponse(BaseModel):
    """Response for model deletion."""

    message: str
    model_id: str


@router.get(
    "/registry",
    response_model=ModelRegistryListResponse,
    summary="List model registry",
    description=(
        "List all models from the database registry with download status. "
        "Use status filter to find downloaded models (status=ready)."
    ),
)
async def list_registry_models(
    db: AsyncSession = Depends(get_db),
    stage: str | None = Query(default=None, description="Filter by stage"),
    runtime: str | None = Query(default=None, description="Filter by runtime"),
    status: str | None = Query(default=None, description="Filter by status"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> ModelRegistryListResponse:
    """List all models from the registry with download status."""
    models = await service.list_models(
        db,
        stage=stage,
        runtime=runtime,
        status=status,
    )

    data = [
        ModelRegistryResponse(
            id=m.id,
            name=m.name,
            runtime=m.runtime,
            runtime_model_id=m.runtime_model_id,
            stage=m.stage,
            status=m.status,
            download_path=m.download_path,
            size_bytes=m.size_bytes,
            downloaded_at=m.downloaded_at,
            source=m.source,
            library_name=m.library_name,
            languages=m.languages,
            word_timestamps=m.word_timestamps,
            punctuation=m.punctuation,
            capitalization=m.capitalization,
            streaming=m.streaming,
            min_vram_gb=m.min_vram_gb,
            min_ram_gb=m.min_ram_gb,
            supports_cpu=m.supports_cpu,
            last_used_at=m.last_used_at,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]

    return ModelRegistryListResponse(data=data)


@router.get(
    "/registry/{model_id}",
    response_model=ModelRegistryResponse,
    summary="Get model registry entry",
    description="Get detailed registry information for a specific model.",
    responses={404: {"description": "Model not found in registry"}},
)
async def get_registry_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> ModelRegistryResponse:
    """Get registry details for a specific model."""
    try:
        model = await service.get_model_or_raise(db, model_id)
    except ModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found in registry: {model_id}",
        ) from None

    return ModelRegistryResponse(
        id=model.id,
        name=model.name,
        runtime=model.runtime,
        runtime_model_id=model.runtime_model_id,
        stage=model.stage,
        status=model.status,
        download_path=model.download_path,
        size_bytes=model.size_bytes,
        downloaded_at=model.downloaded_at,
        source=model.source,
        library_name=model.library_name,
        languages=model.languages,
        word_timestamps=model.word_timestamps,
        punctuation=model.punctuation,
        capitalization=model.capitalization,
        streaming=model.streaming,
        min_vram_gb=model.min_vram_gb,
        min_ram_gb=model.min_ram_gb,
        supports_cpu=model.supports_cpu,
        last_used_at=model.last_used_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.post(
    "/{model_id}/pull",
    response_model=PullModelResponse,
    summary="Download a model",
    description=(
        "Start downloading a model from HuggingFace Hub. "
        "The download runs in the background. Poll GET /v1/models/registry/{model_id} "
        "to check status."
    ),
    responses={404: {"description": "Model not found in registry"}},
)
async def pull_model(
    model_id: str,
    request: PullModelRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> PullModelResponse:
    """Download a model from HuggingFace Hub.

    The download runs asynchronously. Check the model status with
    GET /v1/models/registry/{model_id} to monitor progress.
    """
    force = request.force if request else False

    try:
        model = await service.get_model_or_raise(db, model_id)
    except ModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found in registry: {model_id}",
        ) from None

    if model.status == "ready" and not force:
        return PullModelResponse(
            message="Model already downloaded",
            model_id=model_id,
            status="ready",
        )

    if model.status == "downloading":
        return PullModelResponse(
            message="Model download already in progress",
            model_id=model_id,
            status="downloading",
        )

    # Start download in background
    # Note: We need a new session for the background task
    background_tasks.add_task(_pull_model_background, model_id, force)

    return PullModelResponse(
        message="Download started",
        model_id=model_id,
        status="downloading",
    )


async def _pull_model_background(model_id: str, force: bool) -> None:
    """Background task to download a model."""
    from dalston.db.session import async_session

    service = get_model_registry_service()

    async with async_session() as db:
        try:
            await service.pull_model(db, model_id, force=force)
        except Exception:
            # Error is already logged and status updated in the service
            pass


@router.delete(
    "/{model_id}",
    response_model=DeleteModelResponse,
    summary="Remove downloaded model",
    description="Remove a downloaded model's files from disk.",
    responses={404: {"description": "Model not found in registry"}},
)
async def remove_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> DeleteModelResponse:
    """Remove a downloaded model from disk."""
    try:
        await service.remove_model(db, model_id)
    except ModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found in registry: {model_id}",
        ) from None

    return DeleteModelResponse(
        message="Model removed",
        model_id=model_id,
    )


@router.post(
    "/sync",
    response_model=SyncModelsResponse,
    summary="Sync registry with disk",
    description=(
        "Synchronize the model registry with actual files on disk. "
        "Updates status based on whether model files exist."
    ),
)
async def sync_models(
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> SyncModelsResponse:
    """Sync registry with disk state."""
    result = await service.sync_from_disk(db)
    return SyncModelsResponse(
        updated=result["updated"],
        unchanged=result["unchanged"],
    )


# =============================================================================
# Wildcard routes (must be last to avoid catching specific paths)
# =============================================================================


@router.get(
    "/{model_id}",
    response_model=ModelResponse,
    summary="Get model details",
    description="Get detailed information about a specific model variant.",
    responses={404: {"description": "Model not found in catalog"}},
)
async def get_model(model_id: str) -> ModelResponse:
    """Get details for a specific model variant.

    Args:
        model_id: Model identifier (e.g., 'parakeet-tdt-1.1b', 'faster-whisper-large-v3-turbo')
    """
    catalog = get_catalog()
    model = catalog.get_model(model_id)

    if model is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found: {model_id}. Use GET /v1/models to see available models.",
        )

    return ModelResponse(
        id=model.id,
        name=model.name,
        runtime=model.runtime,
        runtime_model_id=model.runtime_model_id,
        source=model.source,
        size_gb=model.size_gb,
        stage=model.stage,
        languages=model.languages,
        capabilities=ModelCapabilitiesResponse(
            word_timestamps=model.word_timestamps,
            punctuation=model.punctuation,
            capitalization=model.capitalization,
            streaming=False,
        ),
        hardware=ModelHardwareResponse(
            min_vram_gb=model.min_vram_gb,
            supports_cpu=model.supports_cpu,
            min_ram_gb=model.min_ram_gb,
        ),
        performance=ModelPerformanceResponse(
            rtf_gpu=model.rtf_gpu,
            rtf_cpu=model.rtf_cpu,
        ),
    )
