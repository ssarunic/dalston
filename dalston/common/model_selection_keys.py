"""Shared job parameter keys used for engine_id/model selection."""

# Canonical transcription selector key
MODEL_PARAM_TRANSCRIBE = "model_transcribe"

# Stage-specific model selector keys
MODEL_PARAM_DIARIZE = "model_diarize"
MODEL_PARAM_ALIGN = "model_align"
MODEL_PARAM_PII_DETECT = "model_pii_detect"

# Ordered selectors used when checking if a model is in active use by jobs.
ACTIVE_MODEL_SELECTOR_KEYS = (
    MODEL_PARAM_TRANSCRIBE,
    MODEL_PARAM_DIARIZE,
    MODEL_PARAM_ALIGN,
    MODEL_PARAM_PII_DETECT,
)
