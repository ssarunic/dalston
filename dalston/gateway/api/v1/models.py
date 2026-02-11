"""Model discovery API endpoints.

GET /v1/models - List available transcription models
GET /v1/models/{model_id} - Get details for a specific model
"""

from fastapi import APIRouter, HTTPException

from dalston.common.models import (
    MODEL_ALIASES,
    get_available_models,
    resolve_model,
)

router = APIRouter(prefix="/models", tags=["models"])


@router.get(
    "",
    summary="List available models",
    description="List all transcription models that can be used with the `model` parameter.",
)
async def list_models():
    """List available transcription models.

    Returns all models that can be used with the `model` parameter
    in transcription requests.
    """
    models = get_available_models()
    return {
        "object": "list",
        "data": [
            {
                "id": m.id,
                "object": "model",
                "name": m.name,
                "description": m.description,
                "capabilities": {
                    "languages": m.languages,
                    "streaming": m.streaming,
                    "word_timestamps": m.word_timestamps,
                },
                "tier": m.tier,
            }
            for m in models
        ],
        "aliases": MODEL_ALIASES,
    }


@router.get(
    "/{model_id}",
    summary="Get model details",
    description="Get detailed information about a specific model.",
    responses={404: {"description": "Model not found"}},
)
async def get_model(model_id: str):
    """Get details for a specific model.

    Args:
        model_id: Model identifier or alias
    """
    try:
        model = resolve_model(model_id)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Model not found: {model_id}"
        ) from None

    return {
        "id": model.id,
        "object": "model",
        "name": model.name,
        "description": model.description,
        "capabilities": {
            "languages": model.languages,
            "streaming": model.streaming,
            "word_timestamps": model.word_timestamps,
        },
        "tier": model.tier,
        "engine": model.engine,
        "resource_hints": {
            "vram_gb": model.vram_gb,
            "speed_factor": model.speed_factor,
        },
    }
