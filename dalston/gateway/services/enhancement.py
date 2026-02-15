"""Enhancement service for creating batch jobs from realtime sessions.

This service implements the M07 Hybrid Mode functionality, allowing realtime
transcription sessions to trigger batch post-processing for higher quality
results (diarization, word alignment, LLM cleanup, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import JobModel, RealtimeSessionModel
from dalston.gateway.services.jobs import JobsService

if TYPE_CHECKING:
    from dalston.config import Settings

logger = structlog.get_logger()


class EnhancementError(Exception):
    """Error during enhancement job creation."""

    pass


class EnhancementService:
    """Service for creating enhancement jobs from realtime sessions.

    Enhancement jobs take the recorded audio from a realtime session and
    process it through the batch pipeline for:
    - Speaker diarization (identify who said what)
    - Word-level timestamp alignment
    - Optional LLM-based cleanup and formatting
    - Optional emotion detection
    """

    def __init__(self, db: AsyncSession, settings: Settings):
        self.db = db
        self.settings = settings
        self.jobs_service = JobsService()

    async def create_enhancement_job(
        self,
        session: RealtimeSessionModel,
        enhance_diarization: bool = True,
        enhance_word_timestamps: bool = True,
        enhance_llm_cleanup: bool = False,
        enhance_emotions: bool = False,
    ) -> JobModel:
        """Create a batch enhancement job from a realtime session.

        This creates a new batch transcription job using the audio recorded
        during the realtime session, configured for post-processing enhancement.

        Args:
            session: The realtime session to enhance
            enhance_diarization: Enable speaker diarization (default: True)
            enhance_word_timestamps: Enable word-level timestamps (default: True)
            enhance_llm_cleanup: Enable LLM-based cleanup (default: False)
            enhance_emotions: Enable emotion detection (default: False)

        Returns:
            Created JobModel

        Raises:
            EnhancementError: If enhancement cannot be created (e.g., no audio)
        """
        log = logger.bind(session_id=str(session.id))

        # Validate: session must have recorded audio
        if not session.audio_uri:
            raise EnhancementError(
                "Cannot create enhancement job: session has no recorded audio. "
                "Enable store_audio=true when starting the session."
            )

        # Validate: session must be in a terminal state
        if session.status == "active":
            raise EnhancementError(
                "Cannot create enhancement job: session is still active."
            )

        # Validate: session should not already have an enhancement job
        if session.enhancement_job_id is not None:
            raise EnhancementError(
                f"Session already has enhancement job: {session.enhancement_job_id}"
            )

        # Build enhancement job parameters
        # Use a larger model for batch processing when original was a "fast" model
        batch_model = self._get_batch_model(session.model)

        parameters = {
            # Core transcription settings
            "language": session.language or "auto",
            "model": batch_model,
            # Enhancement features
            "speaker_detection": "diarize" if enhance_diarization else "none",
            "timestamps_granularity": "word" if enhance_word_timestamps else "segment",
            "llm_cleanup": enhance_llm_cleanup,
            "emotion_detection": enhance_emotions,
            # Mark this as an enhancement job for tracking
            "_enhancement": {
                "source_session_id": str(session.id),
                "original_model": session.model,
                "original_engine": session.engine,
            },
        }

        log.info(
            "creating_enhancement_job",
            audio_uri=session.audio_uri,
            parameters=parameters,
        )

        # Create the batch job
        job = await self.jobs_service.create_job(
            db=self.db,
            tenant_id=session.tenant_id,
            audio_uri=session.audio_uri,
            parameters=parameters,
            # No webhook for enhancement jobs by default - client polls for status
            webhook_url=None,
            webhook_metadata=None,
        )

        log.info(
            "enhancement_job_created",
            job_id=str(job.id),
        )

        return job

    async def create_enhancement_job_with_audio(
        self,
        session: RealtimeSessionModel,
        audio_uri: str,
        enhance_diarization: bool = True,
        enhance_word_timestamps: bool = True,
        enhance_llm_cleanup: bool = False,
        enhance_emotions: bool = False,
        # PII parameters (M26)
        pii_detection: bool = False,
        pii_detection_tier: str = "standard",
        pii_entity_types: list[str] | None = None,
        redact_pii_audio: bool = False,
        pii_redaction_mode: str = "silence",
    ) -> JobModel:
        """Create a batch enhancement job with an explicit audio URI.

        This method is used when the session's audio_uri hasn't been persisted yet
        (e.g., during the session finalization flow). It avoids mutating the session
        object by accepting the audio URI as a parameter.

        Args:
            session: The realtime session to enhance
            audio_uri: URI of the recorded audio (e.g., s3://bucket/path/audio.wav)
            enhance_diarization: Enable speaker diarization (default: True)
            enhance_word_timestamps: Enable word-level timestamps (default: True)
            enhance_llm_cleanup: Enable LLM-based cleanup (default: False)
            enhance_emotions: Enable emotion detection (default: False)

        Returns:
            Created JobModel

        Raises:
            EnhancementError: If enhancement cannot be created
        """
        log = logger.bind(session_id=str(session.id))

        # Validate: must have audio URI
        if not audio_uri:
            raise EnhancementError(
                "Cannot create enhancement job: no audio URI provided."
            )

        # Note: We don't check session.status == "active" here because this method
        # is called during the finalization flow BEFORE the session status is updated.
        # The caller is responsible for ensuring the session is actually ending.

        # Validate: session should not already have an enhancement job
        if session.enhancement_job_id is not None:
            raise EnhancementError(
                f"Session already has enhancement job: {session.enhancement_job_id}"
            )

        # Build enhancement job parameters
        batch_model = self._get_batch_model(session.model)

        parameters = {
            "language": session.language or "auto",
            "model": batch_model,
            "speaker_detection": "diarize" if enhance_diarization else "none",
            "timestamps_granularity": "word" if enhance_word_timestamps else "segment",
            "llm_cleanup": enhance_llm_cleanup,
            "emotion_detection": enhance_emotions,
            "_enhancement": {
                "source_session_id": str(session.id),
                "original_model": session.model,
                "original_engine": session.engine,
            },
        }

        # Add PII parameters if enabled (M26)
        if pii_detection:
            parameters["pii_detection"] = True
            parameters["pii_detection_tier"] = pii_detection_tier
            if pii_entity_types:
                parameters["pii_entity_types"] = pii_entity_types
            if redact_pii_audio:
                parameters["redact_pii_audio"] = True
                parameters["pii_redaction_mode"] = pii_redaction_mode

        log.info(
            "creating_enhancement_job",
            audio_uri=audio_uri,
            parameters=parameters,
        )

        job = await self.jobs_service.create_job(
            db=self.db,
            tenant_id=session.tenant_id,
            audio_uri=audio_uri,
            parameters=parameters,
            webhook_url=None,
            webhook_metadata=None,
            # PII columns (M26)
            pii_detection_enabled=pii_detection,
            pii_detection_tier=pii_detection_tier if pii_detection else None,
            pii_entity_types=pii_entity_types if pii_detection else None,
            pii_redact_audio=redact_pii_audio,
            pii_redaction_mode=pii_redaction_mode if redact_pii_audio else None,
        )

        log.info(
            "enhancement_job_created",
            job_id=str(job.id),
        )

        return job

    def _get_batch_model(self, realtime_model: str | None) -> str:
        """Map realtime model to appropriate batch model.

        For realtime, users often choose "fast" models for low latency.
        For batch enhancement, we can use larger, more accurate models.

        Args:
            realtime_model: Model used during realtime session

        Returns:
            Model ID to use for batch processing
        """
        # Map fast/distil models to full-size equivalents
        # Default to large-v3 for best quality
        model_mapping = {
            "fast": "large-v3",
            "distil-large-v3-en": "large-v3",
            "distil-whisper-large-v2": "large-v3",
            "parakeet": "large-v3",
            "parakeet-0.6b": "large-v3",
            "parakeet-1.1b": "large-v3",
            "scribe_v1": "large-v3",
            "scribe_v2": "large-v3",
        }

        if realtime_model and realtime_model.lower() in model_mapping:
            return model_mapping[realtime_model.lower()]

        # If already a full model or unknown, default to large-v3
        return "large-v3"


async def create_enhancement_for_session(
    db: AsyncSession,
    settings: Settings,
    session_id: UUID,
    enhance_llm_cleanup: bool = False,
    enhance_emotions: bool = False,
) -> JobModel | None:
    """Convenience function to create enhancement job for a session by ID.

    TODO: This function is currently unused in production but available for
    future use cases where enhancement needs to be triggered programmatically
    (e.g., background workers, scheduled jobs, or admin tools).

    Args:
        db: Database session
        settings: Application settings
        session_id: Session UUID to enhance
        enhance_llm_cleanup: Enable LLM cleanup
        enhance_emotions: Enable emotion detection

    Returns:
        Created JobModel or None if session not found or enhancement failed
    """
    from sqlalchemy import select

    # Fetch the session
    stmt = select(RealtimeSessionModel).where(RealtimeSessionModel.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        logger.warning("enhancement_session_not_found", session_id=str(session_id))
        return None

    service = EnhancementService(db, settings)

    try:
        job = await service.create_enhancement_job(
            session=session,
            enhance_llm_cleanup=enhance_llm_cleanup,
            enhance_emotions=enhance_emotions,
        )
        return job
    except EnhancementError as e:
        logger.warning(
            "enhancement_job_creation_failed",
            session_id=str(session_id),
            error=str(e),
        )
        return None
