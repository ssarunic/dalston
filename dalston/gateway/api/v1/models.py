"""Model catalog API endpoints (M36).

GET /v1/models - List all available model variants
GET /v1/models/{model_id} - Get details for a specific model

NOTE: This endpoint was repurposed in M36. Previously it listed running engines.
For running engine status, use GET /v1/engines instead.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from dalston.orchestrator.catalog import get_catalog

router = APIRouter(prefix="/models", tags=["models"])


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
