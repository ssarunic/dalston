"""Engine discovery API endpoints (M30).

GET /v1/engines - List all engines with status
GET /v1/capabilities - Aggregate capabilities of running engines
GET /v1/lite/capabilities - Machine-readable lite capability matrix (M58)
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.registry import UnifiedEngineRegistry
from dalston.gateway.dependencies import (
    get_db,
    get_principal,
    get_redis,
    get_security_manager,
)
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.model_registry import ModelRegistryService
from dalston.orchestrator.catalog import ExecutionProfile, get_catalog
from dalston.orchestrator.lite_capabilities import (
    LiteProfile,
    check_prerequisites,
    get_active_profile_name,
    get_matrix_as_dict,
)

router = APIRouter(prefix="/engines", tags=["engines"])

# Lite-mode capability discovery router (no prefix; mounted at /v1/lite)
lite_router = APIRouter(prefix="/lite", tags=["lite"])


# Response models


class EngineCapabilitiesResponse(BaseModel):
    """Engine capabilities in API response format."""

    supports_word_timestamps: bool = False
    supports_native_streaming: bool = False
    max_audio_duration_s: int | None = None
    max_concurrency: int | None = None


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


class TaskResponse(BaseModel):
    """Single engine in API response."""

    id: str
    name: str | None = None
    stage: str
    version: str
    execution_profile: ExecutionProfile
    status: Literal["running", "available", "unhealthy"]
    loaded_model: str | None = None  # M36: Currently loaded model (loaded_model_id)
    available_models: list[str] | None = None  # M36: Models on disk, ready to load
    capabilities: EngineCapabilitiesResponse
    hardware: EngineHardwareResponse | None = None
    performance: EnginePerformanceResponse | None = None


class EnginesListResponse(BaseModel):
    """Response for GET /v1/engines."""

    engines: list[TaskResponse]
    total: int


class StageCapabilities(BaseModel):
    """Capabilities for a specific pipeline stage."""

    engines: list[str]
    supports_word_timestamps: bool = False
    supports_native_streaming: bool = False


class AggregateCapabilitiesResponse(BaseModel):
    """Response for GET /v1/capabilities."""

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
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> EnginesListResponse:
    """List all engines with status.

    Merges catalog (what could run) with registry (what's running)
    to provide a complete view of the engine fleet.
    """
    security_manager.require_permission(principal, Permission.MODEL_READ)
    catalog = get_catalog()
    registry = UnifiedEngineRegistry(redis)
    model_service = ModelRegistryService()

    # Load all models from DB, group by engine_id for efficient lookup
    all_models = await model_service.list_models(db)
    models_by_engine_id: dict[str, list[str]] = {}
    for m in all_models:
        if m.engine_id not in models_by_engine_id:
            models_by_engine_id[m.engine_id] = []
        models_by_engine_id[m.engine_id].append(m.id)

    # Get running engines from registry
    running_engines = await registry.get_all()
    running_map = {e.engine_id: e for e in running_engines}

    engines: list[TaskResponse] = []

    # Process all engines from catalog
    for entry in catalog.get_all_engines():
        engine_id = entry.engine_id
        caps = entry.capabilities

        # Determine status and engine_id state from registry
        loaded_model: str | None = None

        if engine_id in running_map:
            reg_engine = running_map[engine_id]
            if reg_engine.is_available:
                status: Literal["running", "available", "unhealthy"] = "running"
            else:
                status = "unhealthy"
            # M36: Include loaded_model from registry heartbeat
            loaded_model = reg_engine.loaded_model
        else:
            status = "available"

        # M36/M46: Get available models for this engine_id from DB
        available_models = models_by_engine_id.get(engine_id) or None

        engines.append(
            TaskResponse(
                id=engine_id,
                name=None,  # Could be added to catalog if needed
                stage=caps.stages[0] if caps.stages else "unknown",
                version=caps.version,
                execution_profile=entry.execution_profile,
                status=status,
                loaded_model=loaded_model,
                available_models=available_models,
                capabilities=EngineCapabilitiesResponse(
                    supports_word_timestamps=caps.supports_word_timestamps,
                    supports_native_streaming=caps.supports_native_streaming,
                    max_audio_duration_s=None,  # Not in current schema
                    max_concurrency=caps.max_concurrency,
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
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    redis: Redis = Depends(get_redis),
) -> AggregateCapabilitiesResponse:
    """Get aggregate capabilities of running engines.

    Combines capabilities from all healthy engines to show
    what the deployment can currently handle.
    """
    security_manager.require_permission(principal, Permission.MODEL_READ)
    catalog = get_catalog()
    registry = UnifiedEngineRegistry(redis)

    # Get running engines from registry
    running_engines = await registry.get_all()
    running_ids = {e.engine_id for e in running_engines if e.is_available}

    # Aggregate capabilities from running engines
    stages: dict[str, StageCapabilities] = {}
    max_duration: int | None = None

    for entry in catalog.get_all_engines():
        if entry.engine_id not in running_ids:
            continue

        caps = entry.capabilities

        # Aggregate by stage
        for stage in caps.stages:
            if stage not in stages:
                stages[stage] = StageCapabilities(
                    engines=[],
                    supports_word_timestamps=False,
                    supports_native_streaming=False,
                )

            stage_caps = stages[stage]
            stage_caps.engines.append(entry.engine_id)

            if caps.supports_word_timestamps:
                stage_caps.supports_word_timestamps = True
            if caps.supports_native_streaming:
                stage_caps.supports_native_streaming = True

    return AggregateCapabilitiesResponse(
        stages=stages,
        max_audio_duration_s=max_duration,
        supported_formats=["wav", "flac", "mp3", "m4a", "ogg", "webm"],
    )


# ---------------------------------------------------------------------------
# Lite capability discovery (M58)
# ---------------------------------------------------------------------------


class LiteProfileSummary(BaseModel):
    """Single profile entry in the lite capability matrix."""

    profile: str
    version: str
    description: str
    stages: list[str]
    supported_options: dict[str, bool]
    requires_prereqs: list[str]


class LiteCapabilitiesResponse(BaseModel):
    """Machine-readable lite capability matrix.

    Derived entirely from ``dalston.orchestrator.lite_capabilities`` — the
    single source of truth.  CLI output, this endpoint, and the docs all
    read from that module, never duplicate it.
    """

    schema_version: str
    default_profile: str
    profile_precedence: list[str]
    profiles: dict[str, LiteProfileSummary]
    active_profile: str
    """Profile currently active in this process (env-var or default)."""
    missing_prereqs: dict[str, list[str]]
    """Per-profile list of prerequisite packages that are not installed."""


@lite_router.get(
    "/capabilities",
    response_model=LiteCapabilitiesResponse,
    summary="Get lite mode capability matrix",
    description=(
        "Return the versioned lite capability matrix derived from the single "
        "source of truth in ``dalston.orchestrator.lite_capabilities``. "
        "Available in all engine_id modes; the ``active_profile`` field reflects "
        "the profile currently configured via env or default."
    ),
)
async def get_lite_capabilities() -> LiteCapabilitiesResponse:
    """Return the full lite capability matrix.

    This endpoint is always available (no Redis/DB dependency) and works in
    both distributed and lite engine_id modes.  It is the primary discovery
    surface for tooling, dashboards, and documentation generators.
    """
    matrix = get_matrix_as_dict()
    active = get_active_profile_name()

    # Check prereqs for each profile so callers know what is actually usable.
    missing: dict[str, list[str]] = {}
    for profile in LiteProfile:
        absent = check_prerequisites(profile)
        if absent:
            missing[profile.value] = absent

    profiles = {
        name: LiteProfileSummary(**data) for name, data in matrix["profiles"].items()
    }

    return LiteCapabilitiesResponse(
        schema_version=matrix["schema_version"],
        default_profile=matrix["default_profile"],
        profile_precedence=matrix["profile_precedence"],
        profiles=profiles,
        active_profile=active,
        missing_prereqs=missing,
    )
