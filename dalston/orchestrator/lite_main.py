"""Lite mode orchestrator with in-memory queue for scoped batch flow."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

import structlog

from dalston.common.queue_backends import InMemoryQueue
from dalston.config import get_settings
from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter
from dalston.orchestrator.lite_capabilities import (
    DEFAULT_PROFILE,
    LitePrerequisiteMissingError,
    LiteProfile,
    check_prerequisites,
    resolve_profile,
    validate_request,
)

logger = structlog.get_logger()


@dataclass
class LiteTask:
    stage: str
    job_id: str


@dataclass
class _SpeakerSegment:
    """Simulated diarisation output for a single segment."""

    text: str
    speaker: str
    start: float = 0.0
    end: float = 0.0


@dataclass
class _PiiEntity:
    """Simulated PII detection output."""

    entity_type: str
    start: int
    end: int
    score: float


class LitePipeline:
    """Profile-aware lite batch pipeline.

    Supported profiles
    ------------------
    core (default)
        prepare → transcribe → merge
    speaker
        prepare → transcribe → diarize → merge
    compliance
        prepare → transcribe → pii_detect → merge
        (only when prerequisite packages are installed)
    """

    def __init__(
        self,
        artifacts: LocalFilesystemArtifactStoreAdapter,
        *,
        profile: str = DEFAULT_PROFILE,
    ) -> None:
        cap = resolve_profile(profile)

        if cap.requires_prereqs:
            missing = check_prerequisites(cap.profile)
            if missing:
                raise LitePrerequisiteMissingError(cap.profile, missing)

        self._queue = InMemoryQueue()
        self._artifacts = artifacts
        self._profile = cap.profile
        self._profile_cap = cap
        logger.info(
            "lite_pipeline_created",
            profile=self._profile.value,
            stages=cap.stages,
        )

    async def run_job(
        self,
        audio_bytes: bytes,
        job_id: str | None = None,
        parameters: dict | None = None,
    ) -> dict:
        """Execute the lite pipeline for *audio_bytes*.

        Args:
            audio_bytes: Raw audio content.
            job_id: Optional stable job ID (generated if omitted).
            parameters: Optional job parameters for validation and stage
                configuration (e.g., ``speaker_detection``, ``num_speakers``).

        Returns:
            Dict with ``job_id`` and ``transcript_uri`` keys.

        Raises:
            LiteUnsupportedFeatureError: If *parameters* request features that
                are not available in the active profile.
            LitePrerequisiteMissingError: If the profile's prerequisites are
                not installed (checked at construction; re-raises here to surface
                in run-time context if pipeline was cached).
        """
        parameters = parameters or {}

        validate_request(self._profile, parameters)

        job_id = job_id or str(uuid4())
        await self._artifacts.write_bytes(
            f"jobs/{job_id}/audio/original.wav", audio_bytes
        )

        logger.info(
            "lite_job_started",
            job_id=job_id,
            profile=self._profile.value,
        )

        result = await self._run_stages(job_id, parameters)

        logger.info(
            "lite_job_completed",
            job_id=job_id,
            profile=self._profile.value,
            transcript_uri=result["transcript_uri"],
        )
        return result

    # ------------------------------------------------------------------
    # Private stage helpers
    # ------------------------------------------------------------------

    async def _run_stages(self, job_id: str, parameters: dict) -> dict:
        """Dispatch to the correct stage sequence for the active profile."""
        if self._profile == LiteProfile.CORE:
            return await self._run_core(job_id, parameters)
        if self._profile == LiteProfile.SPEAKER:
            return await self._run_speaker(job_id, parameters)
        if self._profile == LiteProfile.COMPLIANCE:
            return await self._run_compliance(job_id, parameters)
        # Should never reach here — profiles are exhaustive.
        raise RuntimeError(
            f"Unhandled lite profile: {self._profile!r}"
        )  # pragma: no cover

    async def _run_core(self, job_id: str, parameters: dict) -> dict:
        """core profile: prepare → transcribe → merge."""
        await self._queue.enqueue(
            stage="prepare", task_id=str(uuid4()), job_id=job_id, timeout_s=30
        )
        while True:
            for stage in ("prepare", "transcribe", "merge"):
                envelope = await self._queue.consume(
                    stage=stage, consumer="lite", block_ms=10
                )
                if envelope is None:
                    continue
                if stage == "prepare":
                    await self._queue.enqueue(
                        stage="transcribe",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "transcribe":
                    payload = {
                        "text": "lite transcript",
                        "segments": [{"text": "lite transcript"}],
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/transcribe/output.json",
                        json.dumps(payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="merge",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                else:
                    output_uri = await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/transcript.json",
                        json.dumps(
                            {
                                "job_id": envelope.job_id,
                                "status": "completed",
                                "text": "lite transcript",
                                "profile": LiteProfile.CORE.value,
                                "segments": [{"text": "lite transcript"}],
                            }
                        ).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    return {
                        "job_id": envelope.job_id,
                        "transcript_uri": output_uri,
                    }
                await self._queue.ack(stage=stage, message_id=envelope.message_id)

            await asyncio.sleep(0.01)

    async def _run_speaker(self, job_id: str, parameters: dict) -> dict:
        """speaker profile: prepare → transcribe → diarize → merge."""
        num_speakers = parameters.get("num_speakers") or 2

        await self._queue.enqueue(
            stage="prepare", task_id=str(uuid4()), job_id=job_id, timeout_s=30
        )
        while True:
            for stage in ("prepare", "transcribe", "diarize", "merge"):
                envelope = await self._queue.consume(
                    stage=stage, consumer="lite", block_ms=10
                )
                if envelope is None:
                    continue

                if stage == "prepare":
                    await self._queue.enqueue(
                        stage="transcribe",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "transcribe":
                    payload = {
                        "text": "lite transcript",
                        "segments": [{"text": "lite transcript"}],
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/transcribe/output.json",
                        json.dumps(payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="diarize",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "diarize":
                    # Simulated diarisation output: assign speakers to segments.
                    speakers = [f"SPEAKER_{i:02d}" for i in range(num_speakers)]
                    diarize_payload = {
                        "segments": [
                            {
                                "text": "lite transcript",
                                "speaker": speakers[0],
                                "start": 0.0,
                                "end": 2.0,
                            }
                        ],
                        "speakers": speakers,
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/diarize/output.json",
                        json.dumps(diarize_payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="merge",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                else:  # merge
                    segments_with_speakers = [
                        {
                            "text": "lite transcript",
                            "speaker": "SPEAKER_00",
                            "start": 0.0,
                            "end": 2.0,
                        }
                    ]
                    output_uri = await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/transcript.json",
                        json.dumps(
                            {
                                "job_id": envelope.job_id,
                                "status": "completed",
                                "text": "lite transcript",
                                "profile": LiteProfile.SPEAKER.value,
                                "segments": segments_with_speakers,
                                "speakers": [
                                    f"SPEAKER_{i:02d}" for i in range(num_speakers)
                                ],
                            }
                        ).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    return {
                        "job_id": envelope.job_id,
                        "transcript_uri": output_uri,
                    }
                await self._queue.ack(stage=stage, message_id=envelope.message_id)

            await asyncio.sleep(0.01)

    async def _run_compliance(self, job_id: str, parameters: dict) -> dict:
        """compliance profile: prepare → transcribe → pii_detect → merge."""
        await self._queue.enqueue(
            stage="prepare", task_id=str(uuid4()), job_id=job_id, timeout_s=30
        )
        while True:
            for stage in ("prepare", "transcribe", "pii_detect", "merge"):
                envelope = await self._queue.consume(
                    stage=stage, consumer="lite", block_ms=10
                )
                if envelope is None:
                    continue

                if stage == "prepare":
                    await self._queue.enqueue(
                        stage="transcribe",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "transcribe":
                    payload = {
                        "text": "lite transcript",
                        "segments": [{"text": "lite transcript"}],
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/transcribe/output.json",
                        json.dumps(payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="pii_detect",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "pii_detect":
                    # Simulated PII detection: no entities found in stub data.
                    pii_payload = {
                        "entities": [],
                        "anonymized_text": "lite transcript",
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/pii_detect/output.json",
                        json.dumps(pii_payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="merge",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                else:  # merge
                    output_uri = await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/transcript.json",
                        json.dumps(
                            {
                                "job_id": envelope.job_id,
                                "status": "completed",
                                "text": "lite transcript",
                                "profile": LiteProfile.COMPLIANCE.value,
                                "segments": [{"text": "lite transcript"}],
                                "pii_entities": [],
                            }
                        ).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    return {
                        "job_id": envelope.job_id,
                        "transcript_uri": output_uri,
                    }
                await self._queue.ack(stage=stage, message_id=envelope.message_id)

            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Public factory functions
# ---------------------------------------------------------------------------


def build_pipeline(profile: str = DEFAULT_PROFILE) -> LitePipeline:
    """Build a ``LitePipeline`` for *profile*.

    Args:
        profile: Profile name (``"core"``, ``"speaker"``, ``"compliance"``).

    Returns:
        Ready-to-use ``LitePipeline`` instance.

    Raises:
        RuntimeError: If called outside DALSTON_MODE=lite.
        LiteProfileNotFoundError: If *profile* is not a known profile name.
        LitePrerequisiteMissingError: If the profile's prerequisites are absent.
    """
    settings = get_settings()
    if settings.runtime_mode != "lite":
        raise RuntimeError("Lite pipeline is only available in DALSTON_MODE=lite")
    return LitePipeline(
        LocalFilesystemArtifactStoreAdapter(settings.lite_artifacts_dir),
        profile=profile,
    )


def build_default_pipeline() -> LitePipeline:
    """Build the default (core) lite pipeline.

    Backward-compatible alias for ``build_pipeline("core")``.
    Retained so that callers from M56/M57 continue to work unchanged.
    """
    return build_pipeline(DEFAULT_PROFILE)


async def orchestrator_loop() -> None:
    settings = get_settings()
    if settings.runtime_mode != "lite":
        raise RuntimeError("lite_main can only run in DALSTON_MODE=lite")
    from dalston.db.session import init_db

    await init_db()


def main() -> None:
    asyncio.run(orchestrator_loop())
