"""Engine discovery API endpoints (M30).

GET /v1/engines - List all engines with status
GET /v1/capabilities - Aggregate capabilities of running engines
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

from dalston.gateway.dependencies import RequireJobsRead, get_redis
from dalston.orchestrator.catalog import get_catalog
from dalston.orchestrator.registry import BatchEngineRegistry

router = APIRouter(prefix="/engines", tags=["engines"])


# Response models


class EngineCapabilitiesResponse(BaseModel):
    """Engine capabilities in API response format."""

    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    supports_streaming: bool = False
    max_audio_duration_s: int | None = None


class EngineHardwareResponse(BaseModel):
    """Engine hardware requirements in API response format."""

    gpu_required: bool = False
    min_vram_gb: int | None = None
    supports_cpu: bool = True
    min_ram_gb: int | None = None


class EnginePerformanceResponse(BaseModel):
    """Engine performance characteristics in API response format."""

    rtf_gpu: float | None = None
    rtf_cpu: float | None = None
    max_concurrent_jobs: int | None = None


class EngineResponse(BaseModel):
    """Single engine in API response."""

    id: str
    name: str | None = None
    stage: str
    version: str
    status: Literal["running", "available", "unhealthy"]
    capabilities: EngineCapabilitiesResponse
    hardware: EngineHardwareResponse | None = None
    performance: EnginePerformanceResponse | None = None


class EnginesListResponse(BaseModel):
    """Response for GET /v1/engines."""

    engines: list[EngineResponse]
    total: int


class StageCapabilities(BaseModel):
    """Capabilities for a specific pipeline stage."""

    engines: list[str]
    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    supports_streaming: bool = False


class AggregateCapabilitiesResponse(BaseModel):
    """Response for GET /v1/capabilities."""

    languages: list[str]
    stages: dict[str, StageCapabilities]
    max_audio_duration_s: int | None = None
    supported_formats: list[str]


@router.get(
    "",
    response_model=EnginesListResponse,
    summary="List all engines",
    description=(
        "List all engines with their current status. "
        "Status is 'running' if engine has valid heartbeat, "
        "'available' if in catalog but not running, "
        "'unhealthy' if heartbeat expired."
    ),
)
async def list_engines(
    api_key: RequireJobsRead,
    redis: Redis = Depends(get_redis),
) -> EnginesListResponse:
    """List all engines with status.

    Merges catalog (what could run) with registry (what's running)
    to provide a complete view of the engine fleet.
    """
    catalog = get_catalog()
    registry = BatchEngineRegistry(redis)

    # Get running engines from registry
    running_engines = await registry.get_engines()
    running_map = {e.engine_id: e for e in running_engines}

    engines: list[EngineResponse] = []

    # Process all engines from catalog
    for entry in catalog.get_all_engines():
        engine_id = entry.engine_id
        caps = entry.capabilities

        # Determine status based on registry
        if engine_id in running_map:
            reg_engine = running_map[engine_id]
            if reg_engine.is_available:
                status: Literal["running", "available", "unhealthy"] = "running"
            else:
                status = "unhealthy"
        else:
            status = "available"

        engines.append(
            EngineResponse(
                id=engine_id,
                name=None,  # Could be added to catalog if needed
                stage=caps.stages[0] if caps.stages else "unknown",
                version=caps.version,
                status=status,
                capabilities=EngineCapabilitiesResponse(
                    languages=caps.languages,
                    supports_word_timestamps=caps.supports_word_timestamps,
                    supports_streaming=caps.supports_streaming,
                    max_audio_duration_s=None,  # Not in current schema
                ),
                hardware=EngineHardwareResponse(
                    gpu_required=caps.gpu_required,
                    min_vram_gb=(
                        caps.gpu_vram_mb // 1024 if caps.gpu_vram_mb else None
                    ),
                    supports_cpu=caps.supports_cpu,
                    min_ram_gb=caps.min_ram_gb,
                ),
                performance=EnginePerformanceResponse(
                    rtf_gpu=caps.rtf_gpu,
                    rtf_cpu=caps.rtf_cpu,
                    max_concurrent_jobs=caps.max_concurrent_jobs,
                ),
            )
        )

    return EnginesListResponse(engines=engines, total=len(engines))


@router.get(
    "/capabilities",
    response_model=AggregateCapabilitiesResponse,
    summary="Get aggregate capabilities",
    description=(
        "Get aggregate capabilities of all running engines. "
        "Shows what the current deployment can do."
    ),
)
async def get_capabilities(
    api_key: RequireJobsRead,
    redis: Redis = Depends(get_redis),
) -> AggregateCapabilitiesResponse:
    """Get aggregate capabilities of running engines.

    Combines capabilities from all healthy engines to show
    what the deployment can currently handle.
    """
    catalog = get_catalog()
    registry = BatchEngineRegistry(redis)

    # Get running engines from registry
    running_engines = await registry.get_engines()
    running_ids = {e.engine_id for e in running_engines if e.is_available}

    # Aggregate capabilities from running engines
    all_languages: set[str] = set()
    stages: dict[str, StageCapabilities] = {}
    max_duration: int | None = None

    for entry in catalog.get_all_engines():
        if entry.engine_id not in running_ids:
            continue

        caps = entry.capabilities

        # Aggregate languages
        if caps.languages is None:
            # None means all languages - we can't enumerate them all
            # but we should indicate this somehow
            all_languages.add("*")  # Wildcard to indicate all
        else:
            all_languages.update(caps.languages)

        # Aggregate by stage
        for stage in caps.stages:
            if stage not in stages:
                stages[stage] = StageCapabilities(
                    engines=[],
                    languages=None,
                    supports_word_timestamps=False,
                    supports_streaming=False,
                )

            stage_caps = stages[stage]
            stage_caps.engines.append(entry.engine_id)

            # Merge capabilities (union for booleans, intersection for languages)
            if caps.supports_word_timestamps:
                stage_caps.supports_word_timestamps = True
            if caps.supports_streaming:
                stage_caps.supports_streaming = True

            # Languages: None means all, so if any engine supports all, stage supports all
            if caps.languages is None:
                stage_caps.languages = None
            elif stage_caps.languages is not None:
                # Merge language lists
                existing = set(stage_caps.languages)
                existing.update(caps.languages)
                stage_caps.languages = sorted(existing)

    # Convert wildcard to indication
    languages_list = sorted(all_languages - {"*"})
    if "*" in all_languages:
        # At least one engine supports all languages
        languages_list = ["*"]  # Or could return empty to indicate "all"

    return AggregateCapabilitiesResponse(
        languages=languages_list,
        stages=stages,
        max_audio_duration_s=max_duration,
        supported_formats=["wav", "flac", "mp3", "m4a", "ogg", "webm"],
    )
