"""Realtime side-effect adapter contracts for M51."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

    from dalston.realtime_sdk.session import SessionConfig

logger = structlog.get_logger()


@dataclass(slots=True)
class SessionStorageResult:
    """Result of session storage finalization."""

    audio_artifact_ref: str | None = None
    transcript_artifact_ref: str | None = None


class SessionStorage(ABC):
    """Abstract side-effect boundary for realtime session persistence."""

    @abstractmethod
    async def start(self, session_id: str, config: SessionConfig) -> None:
        """Initialize storage for a session."""

    @abstractmethod
    async def append_audio(self, chunk: bytes) -> None:
        """Append raw audio chunk."""

    @abstractmethod
    async def save_transcript(self, transcript_data: dict[str, Any]) -> None:
        """Buffer or persist transcript payload."""

    @abstractmethod
    async def finalize(self) -> SessionStorageResult:
        """Finalize uploads and return artifact references."""

    @abstractmethod
    async def abort(self) -> None:
        """Abort and cleanup any in-flight resources."""


class S3SessionStorage(SessionStorage):
    """S3-backed session storage adapter."""

    def __init__(
        self,
        *,
        store_audio: bool,
        store_transcript: bool,
    ) -> None:
        self._store_audio = store_audio
        self._store_transcript = store_transcript
        self._s3_context_manager = None
        self._s3_client: S3Client | None = None
        self._audio_recorder = None
        self._transcript_recorder = None
        self._pending_transcript: dict[str, Any] | None = None

    async def start(self, session_id: str, config: SessionConfig) -> None:
        if not self._store_audio and not self._store_transcript:
            return

        from dalston.common.s3 import get_s3_client
        from dalston.config import get_settings
        from dalston.realtime_sdk.audio_recorder import (
            AudioRecorder,
            TranscriptRecorder,
        )

        settings = get_settings()
        bucket = settings.s3_bucket
        if not bucket:
            raise RuntimeError("S3_BUCKET must be configured when storage is enabled")

        self._s3_context_manager = get_s3_client(settings)
        self._s3_client = await self._s3_context_manager.__aenter__()

        if self._store_audio:
            self._audio_recorder = AudioRecorder(
                session_id=session_id,
                s3_client=self._s3_client,
                bucket=bucket,
                sample_rate=config.sample_rate,
            )
            await self._audio_recorder.start()

        if self._store_transcript:
            self._transcript_recorder = TranscriptRecorder(
                session_id=session_id,
                s3_client=self._s3_client,
                bucket=bucket,
            )

    async def append_audio(self, chunk: bytes) -> None:
        if self._audio_recorder:
            await self._audio_recorder.write(chunk)

    async def save_transcript(self, transcript_data: dict[str, Any]) -> None:
        self._pending_transcript = transcript_data

    async def finalize(self) -> SessionStorageResult:
        audio_ref = None
        transcript_ref = None
        if self._audio_recorder:
            audio_ref = await self._audio_recorder.finalize()
        if self._transcript_recorder and self._pending_transcript is not None:
            transcript_ref = await self._transcript_recorder.save(
                self._pending_transcript
            )
        await self.abort()
        return SessionStorageResult(
            audio_artifact_ref=audio_ref,
            transcript_artifact_ref=transcript_ref,
        )

    async def abort(self) -> None:
        if self._audio_recorder and not self._audio_recorder._finalized:
            try:
                await self._audio_recorder.abort()
            except Exception:
                logger.debug("session_storage_audio_abort_failed")
        if self._s3_context_manager:
            try:
                await self._s3_context_manager.__aexit__(None, None, None)
            except Exception:
                logger.debug("session_storage_context_exit_failed")
        self._s3_context_manager = None
        self._s3_client = None
