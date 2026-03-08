"""Centralized error codes and messages for HTTP API responses.

Usage:

    from dalston.gateway.error_codes import Err

    # Simple usage — string detail:
    raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

    # Structured detail (for endpoints that return {code, message}):
    raise HTTPException(status_code=404, detail=Err.structured("job_not_found"))

    # Dynamic values — use the message as a template:
    raise HTTPException(
        status_code=400,
        detail=Err.CONCURRENT_JOB_LIMIT.format(limit=result.limit),
    )

All user-facing API error strings live here. Keep them alphabetized within
each section for easy scanning.
"""


class Err:
    """API error message constants.

    Organized by HTTP status code, then alphabetically.
    Use ALL_CAPS for static messages, methods for structured/dynamic ones.
    """

    # -------------------------------------------------------------------------
    # 400 Bad Request
    # -------------------------------------------------------------------------
    CANNOT_REVOKE_OWN_KEY = "Cannot revoke your own API key"
    FILE_MUST_HAVE_FILENAME = "File must have a filename"
    INVALID_CURSOR_FORMAT = "Invalid cursor format"
    INVALID_DATETIME_SINCE = "Invalid 'since' datetime format"
    INVALID_DATETIME_UNTIL = "Invalid 'until' datetime format"
    INVALID_SCOPE = "Invalid scope. Valid scopes: {valid_scopes}"
    JOB_NOT_COMPLETED = "Job not completed. Current status: {status}"
    KEYTERMS_EXCEED_LIMIT = "keyterms cannot exceed 100 terms"
    KEYTERMS_INVALID_JSON = "Invalid JSON in keyterms: {error}"
    KEYTERMS_MUST_BE_ARRAY = "keyterms must be a JSON array of strings"
    KEYTERMS_MUST_BE_STRINGS = "keyterms must contain only strings"
    KEYTERM_TOO_LONG = "Each keyterm must be at most 50 characters, got {length}"
    PER_CHANNEL_REQUIRES_STEREO = (
        "per_channel speaker detection requires stereo audio, "
        "got {channels} channel(s)"
    )
    PROVIDE_FILE_OR_URL = "Either 'file' or 'audio_url' must be provided"
    PROVIDE_FILE_OR_URL_NOT_BOTH = "Provide either 'file' or 'audio_url'"
    OPENAI_FILE_TOO_LARGE = "File size exceeds 25MB limit ({size_mb:.1f}MB)"
    OPENAI_PER_CHANNEL_REQUIRES_STEREO = (
        "per_channel speaker detection requires stereo audio, "
        "but file has {channels} channel(s)."
    )
    OPENAI_TRANSCRIPTION_TIMEOUT = (
        "Transcription timeout. The audio file may be too long."
    )
    TRANSCRIPT_LOAD_FAILED = (
        "Transcription completed but transcript could not be loaded."
    )
    UNSUPPORTED_FORMAT = "Unsupported format: {format_str}. Supported formats: {valid_formats}"
    TRANSCRIPTION_CANCELLED = "Transcription was cancelled"
    TRANSCRIPTION_NOT_COMPLETED = "Transcription not completed. Current status: {status}"
    VOCABULARY_EXCEED_LIMIT = "vocabulary cannot exceed 100 terms"
    VOCABULARY_INVALID_JSON = "Invalid JSON in vocabulary: {error}"
    VOCABULARY_MUST_BE_ARRAY = "vocabulary must be a JSON array of strings"
    VOCABULARY_MUST_BE_STRINGS = "vocabulary must contain only strings"

    # -------------------------------------------------------------------------
    # 403 Forbidden
    # -------------------------------------------------------------------------
    KEY_REQUIRES_REALTIME_SCOPE = (
        "API key requires 'realtime' scope to create session tokens"
    )
    MISSING_SCOPE = "Missing required scope: {scope}"

    # -------------------------------------------------------------------------
    # 404 Not Found
    # -------------------------------------------------------------------------
    API_KEY_NOT_FOUND = "API key not found"
    AUDIO_NOT_AVAILABLE = "Audio not available"
    AUDIO_NOT_FOUND = "Audio not found"
    DELIVERY_NOT_FOUND = "Delivery not found"
    FAILED_AUDIO_DOWNLOAD_URL = "Failed to generate audio download URL"
    FAILED_REDACTED_AUDIO_DOWNLOAD_URL = (
        "Failed to generate redacted audio download URL"
    )
    INVALID_AUDIO_URI = "Invalid audio URI"
    JOB_NOT_FOUND = "Job not found"
    MODEL_NOT_FOUND = "Model not found in registry: {model_id}"
    MODEL_NOT_ON_HF = "Model not found on HuggingFace Hub: {model_id}"
    MODEL_NOT_FOUND_HINT = (
        "Model not found: {model_id}. Use GET /v1/models to see available models."
    )
    NAMESPACE_NOT_FOUND = "Unknown namespace: {namespace}"
    ORIGINAL_AUDIO_NOT_FOUND = "Original audio not found"
    PII_NOT_ENABLED = "PII audio redaction was not enabled for this job"
    PII_METADATA_NOT_AVAILABLE = "PII metadata not available in transcript"
    REDACTED_AUDIO_INCOMPLETE = (
        "Redacted audio not available. "
        "PII redaction may not have completed successfully."
    )
    REDACTED_AUDIO_NOT_FOUND = "Redacted audio not found"
    SESSION_NOT_FOUND = "Session not found"
    TASK_NOT_FOUND = "Task not found"
    TRANSCRIPT_NOT_AVAILABLE = "Transcript not available"
    TRANSCRIPT_NOT_FOUND = "Transcript not found"
    TRANSCRIPTION_NOT_FOUND = "Transcription not found"
    WEBHOOK_NOT_FOUND = "Webhook endpoint not found"
    WORKER_NOT_FOUND = "Worker not found"

    # -------------------------------------------------------------------------
    # 408 Request Timeout
    # -------------------------------------------------------------------------
    TRANSCRIPTION_TIMEOUT = (
        "Transcription timeout. Use webhook=true for long files."
    )

    # -------------------------------------------------------------------------
    # 409 Conflict
    # -------------------------------------------------------------------------
    JOB_NOT_TERMINAL = "Job not in terminal state. Current status: {status}"
    REDACTED_AUDIO_REQUIRES_COMPLETED = (
        "Job not completed. Current status: {status}. "
        "Redacted audio is only available for completed jobs."
    )

    # -------------------------------------------------------------------------
    # 410 Gone
    # -------------------------------------------------------------------------
    AUDIO_ALREADY_PURGED = "Audio already purged"
    AUDIO_DELETED = "Audio has been deleted"
    AUDIO_PURGED = "Audio has been purged according to retention policy"

    # -------------------------------------------------------------------------
    # 429 Too Many Requests
    # -------------------------------------------------------------------------
    RATE_LIMIT_EXCEEDED = "Rate limit exceeded"
    CONCURRENT_JOB_LIMIT = "Concurrent job limit exceeded ({limit} max)"
    CONCURRENT_SESSION_LIMIT = "Concurrent session limit exceeded ({limit} max)"

    # -------------------------------------------------------------------------
    # 500 Internal Server Error
    # -------------------------------------------------------------------------
    LITE_TRANSCRIPTION_FAILED = "Lite transcription failed: {error}"
    TRANSCRIPTION_FAILED = "Transcription failed: {error}"

    # -------------------------------------------------------------------------
    # 503 Service Unavailable
    # -------------------------------------------------------------------------
    SESSION_ROUTER_NOT_INITIALIZED = "Session router not initialized"

    # -------------------------------------------------------------------------
    # Task artifacts (structured error codes used in detail dicts)
    # -------------------------------------------------------------------------
    # These map error codes to messages for endpoints that return
    # {"code": "...", "message": "..."} in the detail field.
    _STRUCTURED: dict[str, str] = {
        "job_not_found": JOB_NOT_FOUND,
        "task_not_found": TASK_NOT_FOUND,
        "no_artifacts": "Task has not started yet",
        "audio_deleted": AUDIO_DELETED,
        "audio_purged": AUDIO_PURGED,
    }

    @classmethod
    def structured(
        cls,
        code: str,
        *,
        message: str | None = None,
        **extra: str,
    ) -> dict[str, str]:
        """Build a structured error detail dict.

        Args:
            code: Machine-readable error code (e.g. "job_not_found").
            message: Override the default message for this code.
            **extra: Additional fields to include (e.g. purged_at).

        Returns:
            Dict like {"code": "job_not_found", "message": "Job not found"}.
        """
        msg = message or cls._STRUCTURED.get(code, code)
        result: dict[str, str] = {"code": code, "message": msg}
        result.update(extra)
        return result
