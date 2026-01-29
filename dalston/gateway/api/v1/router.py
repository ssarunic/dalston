"""V1 API router - aggregates all v1 routes."""

from fastapi import APIRouter

from dalston.gateway.api.v1 import transcription

router = APIRouter(prefix="/v1")

# Mount transcription routes
router.include_router(transcription.router)
