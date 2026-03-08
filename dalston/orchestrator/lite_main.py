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

# Maximum wall-clock seconds allowed for a single lite job across all stages.
_JOB_TIMEOUT_S = 120.0


@dataclass
class LiteTask:
    stage: str
    job_id: str


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

    # Stage sequences keyed by profile.
    _STAGES: dict[LiteProfile, tuple[str, ...]] = {
        LiteProfile.CORE: ("prepare", "transcribe", "merge"),
        LiteProfile.SPEAKER: ("prepare", "transcribe", "diarize", "merge"),
        LiteProfile.COMPLIANCE: ("prepare", "transcribe", "pii_detect", "merge"),
    }

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

        result = await self._run_loop(job_id, parameters)

        logger.info(
            "lite_job_completed",
            job_id=job_id,
            profile=self._profile.value,
            transcript_uri=result["transcript_uri"],
        )
        return result

    # ------------------------------------------------------------------
    # Shared stage loop
    # ------------------------------------------------------------------

    async def _run_loop(self, job_id: str, parameters: dict) -> dict:
        """Drive the stage queue for the active profile with a deadline.

        Enqueues the ``prepare`` task, then polls each stage in order until
        ``merge`` completes and returns the transcript URI.  Raises
        ``asyncio.TimeoutError`` if the job exceeds ``_JOB_TIMEOUT_S``.
        """
        stages = self._STAGES[self._profile]
        await self._queue.enqueue(
            stage="prepare", task_id=str(uuid4()), job_id=job_id, timeout_s=30
        )
        async with asyncio.timeout(_JOB_TIMEOUT_S):
            while True:
                for stage in stages:
                    envelope = await self._queue.consume(
                        stage=stage, consumer="lite", block_ms=10
                    )
                    if envelope is None:
                        continue
                    result = await self._handle_stage(stage, envelope, parameters)
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    if result is not None:
                        return result
                await asyncio.sleep(0.01)

    async def _handle_stage(
        self, stage: str, envelope: object, parameters: dict
    ) -> dict | None:
        """Process one stage envelope.

        Returns the final result dict when ``merge`` completes; ``None`` for
        all other stages so the loop continues.
        """
        job_id: str = envelope.job_id  # type: ignore[attr-defined]

        if stage == "prepare":
            # Next stage is always transcribe regardless of profile.
            await self._queue.enqueue(
                stage="transcribe",
                task_id=str(uuid4()),
                job_id=job_id,
                timeout_s=30,
            )
            return None

        if stage == "transcribe":
            payload = {
                "text": "lite transcript",
                "segments": [{"text": "lite transcript"}],
            }
            await self._artifacts.write_bytes(
                f"jobs/{job_id}/tasks/transcribe/output.json",
                json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            )
            # Next stage depends on profile.
            next_stage = {
                LiteProfile.CORE: "merge",
                LiteProfile.SPEAKER: "diarize",
                LiteProfile.COMPLIANCE: "pii_detect",
            }[self._profile]
            await self._queue.enqueue(
                stage=next_stage,
                task_id=str(uuid4()),
                job_id=job_id,
                timeout_s=30,
            )
            return None

        if stage == "diarize":
            num_speakers = parameters.get("num_speakers") or 2
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
                f"jobs/{job_id}/tasks/diarize/output.json",
                json.dumps(diarize_payload).encode("utf-8"),
                content_type="application/json",
            )
            await self._queue.enqueue(
                stage="merge",
                task_id=str(uuid4()),
                job_id=job_id,
                timeout_s=30,
            )
            return None

        if stage == "pii_detect":
            pii_payload = {
                "entities": [],
                "anonymized_text": "lite transcript",
            }
            await self._artifacts.write_bytes(
                f"jobs/{job_id}/tasks/pii_detect/output.json",
                json.dumps(pii_payload).encode("utf-8"),
                content_type="application/json",
            )
            await self._queue.enqueue(
                stage="merge",
                task_id=str(uuid4()),
                job_id=job_id,
                timeout_s=30,
            )
            return None

        # merge — assemble profile-specific transcript and return.
        return await self._handle_merge(job_id, parameters)

    async def _handle_merge(self, job_id: str, parameters: dict) -> dict:
        """Write the final transcript artifact and return the result dict."""
        if self._profile == LiteProfile.SPEAKER:
            num_speakers = parameters.get("num_speakers") or 2
            transcript: dict = {
                "job_id": job_id,
                "status": "completed",
                "text": "lite transcript",
                "profile": LiteProfile.SPEAKER.value,
                "segments": [
                    {
                        "text": "lite transcript",
                        "speaker": "SPEAKER_00",
                        "start": 0.0,
                        "end": 2.0,
                    }
                ],
                "speakers": [f"SPEAKER_{i:02d}" for i in range(num_speakers)],
            }
        elif self._profile == LiteProfile.COMPLIANCE:
            transcript = {
                "job_id": job_id,
                "status": "completed",
                "text": "lite transcript",
                "profile": LiteProfile.COMPLIANCE.value,
                "segments": [{"text": "lite transcript"}],
                "pii_entities": [],
            }
        else:  # core
            transcript = {
                "job_id": job_id,
                "status": "completed",
                "text": "lite transcript",
                "profile": LiteProfile.CORE.value,
                "segments": [{"text": "lite transcript"}],
            }

        output_uri = await self._artifacts.write_bytes(
            f"jobs/{job_id}/transcript.json",
            json.dumps(transcript).encode("utf-8"),
            content_type="application/json",
        )
        return {"job_id": job_id, "transcript_uri": output_uri}


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
    # Deferred to avoid a circular import: dalston.db.session imports Settings
    # indirectly via dalston.config, which is not fully initialised at the
    # time this module is first imported.
    from dalston.db.session import init_db

    await init_db()


def main() -> None:
    asyncio.run(orchestrator_loop())
