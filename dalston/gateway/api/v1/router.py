"""V1 API router - aggregates all v1 routes."""

from fastapi import APIRouter

from dalston.gateway.api.v1 import speech_to_text, transcription

router = APIRouter(prefix="/v1")

# Mount transcription routes (Dalston native API)
router.include_router(transcription.router)

# Mount speech-to-text routes (ElevenLabs compatible API)
router.include_router(speech_to_text.router)
