"""V1 API router - aggregates all v1 routes."""

from fastapi import APIRouter

from dalston.gateway.api.v1 import (
    jobs,
    realtime,
    speech_to_text,
    tasks,
    transcription,
    webhooks,
)

router = APIRouter(prefix="/v1")

# Mount transcription routes (Dalston native API)
router.include_router(transcription.router)

# Mount tasks routes (task observability - nested under transcriptions)
router.include_router(tasks.router)

# Mount jobs routes (stats endpoint)
router.include_router(jobs.router)

# Mount speech-to-text routes (ElevenLabs compatible API)
router.include_router(speech_to_text.router)

# Mount real-time transcription routes
router.include_router(realtime.stream_router)  # WS /v1/audio/transcriptions/stream
router.include_router(realtime.management_router)  # GET /v1/realtime/*

# Mount webhook management routes
router.include_router(webhooks.router)  # /v1/webhooks/*
