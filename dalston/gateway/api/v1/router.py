"""V1 API router - aggregates all v1 routes."""

from fastapi import APIRouter

from dalston.gateway.api.v1 import (
    audit,
    engines,
    jobs,
    models,
    openai_realtime,
    openai_translation,
    pii,
    realtime,
    realtime_sessions,
    realtime_status,
    speech_to_text,
    tasks,
    transcription,
    webhooks,
)

router = APIRouter(prefix="/v1")

# Mount model discovery routes
router.include_router(models.router)

# Mount engine discovery routes (M30)
router.include_router(engines.router)

# Mount transcription routes (Dalston native API + OpenAI compatible via model detection)
router.include_router(transcription.router)

# Mount OpenAI translation endpoint (M38)
router.include_router(openai_translation.router)  # POST /v1/audio/translations

# Mount tasks routes (task observability - nested under transcriptions)
router.include_router(tasks.router)

# Mount jobs routes (stats endpoint)
router.include_router(jobs.router)

# Mount speech-to-text routes (ElevenLabs compatible API)
router.include_router(speech_to_text.router)

# Mount real-time transcription routes
router.include_router(realtime.stream_router)  # WS /v1/audio/transcriptions/stream
router.include_router(
    realtime.elevenlabs_router
)  # WS /v1/speech-to-text/realtime (ElevenLabs)
router.include_router(
    openai_realtime.openai_realtime_router
)  # WS /v1/realtime (OpenAI)
router.include_router(realtime_status.router)  # GET /v1/realtime/status, /workers
router.include_router(realtime_sessions.router)  # GET /v1/realtime/sessions/*

# Mount webhook management routes
router.include_router(webhooks.router)  # /v1/webhooks/*

# Mount audit log routes (M25)
router.include_router(audit.router)  # /v1/audit/*

# Mount PII detection routes (M26)
router.include_router(pii.router)  # /v1/pii/*
