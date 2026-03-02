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
    ModelInUseError,
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


class ModelMetadataResponse(BaseModel):
    """Model metadata from HuggingFace or other sources."""

    downloads: int | None = None
    likes: int | None = None
    tags: list[str] | None = None
    pipeline_tag: str | None = None
    error: str | None = None


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
    download_progress: int | None = None  # Percentage (0-100) when downloading
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
    metadata: ModelMetadataResponse = ModelMetadataResponse()
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


def _build_metadata_response(model_metadata: dict | None) -> ModelMetadataResponse:
    """Convert DB model_metadata dict to API response model."""
    if not model_metadata:
        return ModelMetadataResponse()
    return ModelMetadataResponse(
        downloads=model_metadata.get("downloads"),
        likes=model_metadata.get("likes"),
        tags=model_metadata.get("tags"),
        pipeline_tag=model_metadata.get("pipeline_tag"),
        error=model_metadata.get("error"),
    )


@router.get(
    "/registry",
    response_model=ModelRegistryListResponse,
    summary="List model registry",
    description=(
        "List all models from the database registry with download status. "
        "Use status filter to find downloaded models (status=ready). "
        "Pass sync=true to sync with disk before returning results."
    ),
)
async def list_registry_models(
    db: AsyncSession = Depends(get_db),
    stage: str | None = Query(default=None, description="Filter by stage"),
    runtime: str | None = Query(default=None, description="Filter by runtime"),
    status: str | None = Query(default=None, description="Filter by status"),
    sync: bool = Query(default=False, description="Sync with disk before listing"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> ModelRegistryListResponse:
    """List all models from the registry with download status."""
    # Optionally sync with disk first to detect engine-downloaded models
    if sync:
        await service.sync_from_disk(db)

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
            download_progress=None,  # Progress tracking not yet implemented
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
            metadata=_build_metadata_response(m.model_metadata),
            last_used_at=m.last_used_at,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]

    return ModelRegistryListResponse(data=data)


@router.get(
    "/registry/{model_id:path}",
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
        download_progress=None,  # Progress tracking not yet implemented
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
        metadata=_build_metadata_response(model.model_metadata),
        last_used_at=model.last_used_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


@router.post(
    "/{model_id:path}/pull",
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

    # Update status to downloading immediately so UI sees the change
    await service.set_model_status(db, model_id, "downloading")

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
    "/{model_id:path}",
    response_model=DeleteModelResponse,
    summary="Remove or delete model",
    description=(
        "Remove a model's downloaded files from disk. "
        "Pass purge=true to also delete the model from the registry entirely."
    ),
    responses={
        404: {"description": "Model not found in registry"},
        409: {"description": "Model is in use by pending jobs"},
    },
)
async def remove_model(
    model_id: str,
    purge: bool = Query(
        default=False,
        description="If true, delete model from registry entirely. If false, only remove files.",
    ),
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> DeleteModelResponse:
    """Remove a downloaded model from disk, optionally deleting from registry."""
    try:
        await service.remove_model(db, model_id, purge=purge)
    except ModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found in registry: {model_id}",
        ) from None
    except ModelInUseError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
        ) from None

    message = "Model deleted from registry" if purge else "Model files removed"
    return DeleteModelResponse(
        message=message,
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
# M40.5: HuggingFace Card Routing Endpoints
# =============================================================================


class HFResolveRequest(BaseModel):
    """Request body for HuggingFace model resolution."""

    model_id: str
    auto_register: bool = False


class HFModelMetadataResponse(BaseModel):
    """Response for HuggingFace model metadata."""

    model_id: str
    library_name: str | None = None
    pipeline_tag: str | None = None
    tags: list[str] = []
    languages: list[str] = []
    downloads: int = 0
    likes: int = 0
    resolved_runtime: str | None = None
    can_route: bool = False


class HFRoutingMappingsResponse(BaseModel):
    """Response for HuggingFace routing mappings."""

    library_to_runtime: dict[str, str]
    tag_to_runtime: dict[str, str]
    supported_runtimes: list[str]


@router.post(
    "/hf/resolve",
    response_model=HFModelMetadataResponse,
    summary="Resolve HuggingFace model",
    description=(
        "Fetch metadata from HuggingFace Hub and determine which Dalston runtime "
        "can load the model. Uses library_name, tags, and pipeline_tag for routing."
    ),
    responses={
        200: {"description": "Model metadata with resolved runtime"},
        404: {"description": "Model not found on HuggingFace Hub"},
    },
)
async def resolve_hf_model(
    request: HFResolveRequest,
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> HFModelMetadataResponse:
    """Resolve a HuggingFace model ID to determine compatible runtime.

    This endpoint:
    1. Fetches model info from HuggingFace Hub
    2. Extracts library_name, tags, and pipeline_tag
    3. Determines which Dalston runtime can load the model
    4. Optionally auto-registers the model in the registry

    The routing priority is:
    1. library_name (most reliable) - e.g., "ctranslate2" -> "faster-whisper"
    2. Model tags (fallback) - e.g., "nemo" tag -> "nemo" runtime
    3. pipeline_tag (last resort) - "automatic-speech-recognition" -> "hf-asr"
    """
    from dalston.gateway.services.hf_resolver import HFResolver

    resolver = HFResolver()
    metadata = await resolver.get_model_metadata(request.model_id)

    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found on HuggingFace Hub: {request.model_id}",
        )

    # Auto-register if requested and runtime was resolved
    if request.auto_register and metadata.resolved_runtime:
        # Check if model already exists in registry (by ID or by runtime_model_id)
        existing = await service.get_model(db, request.model_id)
        if existing is None:
            # Also check if a model with this runtime_model_id already exists
            # (catalog models may have different IDs but same runtime_model_id)
            from sqlalchemy import select

            from dalston.db.models import ModelRegistryModel

            stmt = select(ModelRegistryModel).where(
                ModelRegistryModel.runtime_model_id == request.model_id
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

        if existing is None:
            await service.register_model(
                db,
                model_id=request.model_id,
                runtime=metadata.resolved_runtime,
                runtime_model_id=request.model_id,
                stage="transcribe",
                source=request.model_id,
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

    return HFModelMetadataResponse(
        model_id=metadata.model_id,
        library_name=metadata.library_name,
        pipeline_tag=metadata.pipeline_tag,
        tags=metadata.tags[:20],  # Limit response tags
        languages=metadata.languages,
        downloads=metadata.downloads,
        likes=metadata.likes,
        resolved_runtime=metadata.resolved_runtime,
        can_route=metadata.resolved_runtime is not None,
    )


@router.get(
    "/hf/mappings",
    response_model=HFRoutingMappingsResponse,
    summary="Get HuggingFace routing mappings",
    description="Get the library_name and tag mappings used for HuggingFace model routing.",
)
async def get_hf_routing_mappings() -> HFRoutingMappingsResponse:
    """Return the routing mappings used for HuggingFace model resolution.

    Useful for understanding which HuggingFace models can be auto-routed
    to Dalston engines.
    """
    from dalston.gateway.services.hf_resolver import HFResolver

    resolver = HFResolver()
    return HFRoutingMappingsResponse(
        library_to_runtime=resolver.get_library_to_runtime_mapping(),
        tag_to_runtime=resolver.get_tag_to_runtime_mapping(),
        supported_runtimes=resolver.get_supported_runtimes(),
    )


# =============================================================================
# Wildcard routes (must be last to avoid catching specific paths)
# =============================================================================


@router.get(
    "/{model_id:path}",
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
