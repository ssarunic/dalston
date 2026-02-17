"""Engine discovery API endpoints.

GET /v1/models - List running transcription engines
GET /v1/models/{engine_id} - Get details for a specific running engine
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis

from dalston.gateway.dependencies import get_redis
from dalston.orchestrator.registry import BatchEngineRegistry

router = APIRouter(prefix="/models", tags=["models"])


@router.get(
    "",
    summary="List running engines",
    description="List transcription engines that are currently running and available for use.",
)
async def list_models(
    stage: str | None = Query(
        default="transcribe",
        description="Filter by pipeline stage (transcribe, diarize, align, etc.)",
    ),
    redis: Redis = Depends(get_redis),
):
    """List running transcription engines.

    Returns engines that are currently running and can be used with the `model` parameter
    in transcription requests. Use 'auto' to let the system select the best engine.
    """
    registry = BatchEngineRegistry(redis)

    if stage:
        engines = await registry.get_engines_for_stage(stage)
    else:
        engines = await registry.get_engines()

    # Filter to only available (healthy) engines
    available_engines = [e for e in engines if e.is_available]

    return {
        "object": "list",
        "data": [
            {
                "id": e.engine_id,
                "object": "model",
                "stage": e.stage,
                "status": "running",
                "capabilities": {
                    "languages": e.capabilities.languages if e.capabilities else None,
                    "streaming": (
                        e.capabilities.supports_streaming if e.capabilities else False
                    ),
                    "word_timestamps": (
                        e.capabilities.supports_word_timestamps
                        if e.capabilities
                        else False
                    ),
                },
            }
            for e in available_engines
        ],
    }


@router.get(
    "/{engine_id}",
    summary="Get engine details",
    description="Get detailed information about a specific running engine.",
    responses={404: {"description": "Engine not found or not running"}},
)
async def get_model(
    engine_id: str,
    redis: Redis = Depends(get_redis),
):
    """Get details for a specific running engine.

    Args:
        engine_id: Engine identifier (e.g., faster-whisper-base, parakeet-0.6b)
    """
    registry = BatchEngineRegistry(redis)
    engine = await registry.get_engine(engine_id)

    if engine is None or not engine.is_available:
        raise HTTPException(
            status_code=404, detail=f"Engine not running: {engine_id}"
        ) from None

    caps = engine.capabilities
    return {
        "id": engine.engine_id,
        "object": "model",
        "stage": engine.stage,
        "status": "running",
        "capabilities": {
            "languages": caps.languages if caps else None,
            "streaming": caps.supports_streaming if caps else False,
            "word_timestamps": caps.supports_word_timestamps if caps else False,
        },
    }
