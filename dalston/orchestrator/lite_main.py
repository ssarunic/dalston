"""Lite mode orchestrator with in-memory queue for scoped batch flow."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from dalston.common.queue_backends import InMemoryQueue, QueueEnvelope
from dalston.config import Settings, get_settings
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.executors import (
    ExecutionRequest,
    InProcExecutor,
    RuntimeExecutor,
    VenvEnvironmentManager,
    VenvExecutor,
)
from dalston.engine_sdk.types import EngineCapabilities, EngineInput, EngineOutput
from dalston.gateway.services.artifact_store import (
    ArtifactStore,
    InMemoryArtifactStoreAdapter,
    LocalFilesystemArtifactStoreAdapter,
)
from dalston.orchestrator.catalog import CatalogEntry
from dalston.orchestrator.lite_capabilities import (
    DEFAULT_PROFILE,
    LitePrerequisiteMissingError,
    LiteProfile,
    check_prerequisites,
    resolve_profile,
    validate_request,
)
from dalston.orchestrator.lite_messages import LiteMsg

logger = structlog.get_logger()

# Maximum wall-clock seconds allowed for a single lite job across all stages.
_JOB_TIMEOUT_S = 120.0
_DEFAULT_LITE_TRANSCRIBE_RUNTIME = "faster-whisper"


# ---------------------------------------------------------------------------
# Stage compute helpers — synchronous, safe to run in asyncio.to_thread()
# ---------------------------------------------------------------------------
# These functions encapsulate the CPU-bound work for each pipeline stage.
# In real engines this is model inference; in the lite stub it is trivial, but
# the asyncio.to_thread() wrapper ensures the pattern is correct so that
# plugging in a real model never accidentally blocks the event loop.


def _compute_transcribe(parameters: dict) -> dict:  # noqa: ARG001
    return {
        "text": "lite transcript",
        "segments": [{"text": "lite transcript"}],
    }


def _compute_diarize(parameters: dict) -> dict:
    num_speakers = parameters.get("num_speakers") or 2
    speakers = [f"SPEAKER_{i:02d}" for i in range(num_speakers)]
    return {
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


def _compute_pii_detect(parameters: dict) -> dict:  # noqa: ARG001
    return {
        "entities": [],
        "anonymized_text": "lite transcript",
    }


class _LiteComputeEngine(Engine[Any, Any]):
    """Adapter that preserves the existing lite stage outputs behind Engine.process."""

    def __init__(self, compute: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        super().__init__()
        self._compute = compute

    def process(
        self,
        engine_input: EngineInput,
        ctx: BatchTaskContext,
    ) -> EngineOutput:
        del ctx
        return EngineOutput(data=self._compute(engine_input.config))


@dataclass(frozen=True)
class _LiteStageBinding:
    entry: CatalogEntry
    engine_factory: Callable[[], Engine[Any, Any]] | None = None
    engine_ref: str | None = None


def _make_stage_binding(
    *,
    stage: str,
    runtime: str,
    compute: Callable[[dict[str, Any]], dict[str, Any]],
    execution_profile: str = "inproc",
) -> _LiteStageBinding:
    return _LiteStageBinding(
        entry=CatalogEntry(
            runtime=runtime,
            image=f"dalston/lite-{stage}:{execution_profile}",
            capabilities=EngineCapabilities(
                runtime=runtime,
                version="lite",
                stages=[stage],
            ),
            execution_profile=execution_profile,
        ),
        engine_factory=lambda: _LiteComputeEngine(compute),
    )


def _make_engine_ref_binding(
    *,
    stage: str,
    runtime: str,
    engine_ref: str,
    execution_profile: str = "venv",
) -> _LiteStageBinding:
    return _LiteStageBinding(
        entry=CatalogEntry(
            runtime=runtime,
            image=f"dalston/lite-{stage}:{execution_profile}",
            capabilities=EngineCapabilities(
                runtime=runtime,
                version="lite",
                stages=[stage],
            ),
            execution_profile=execution_profile,
        ),
        engine_ref=engine_ref,
    )


def _build_default_stage_bindings(settings: Settings) -> dict[str, _LiteStageBinding]:
    if settings.lite_transcribe_backend == "real":
        transcribe = _make_engine_ref_binding(
            stage="transcribe",
            runtime=_DEFAULT_LITE_TRANSCRIBE_RUNTIME,
            engine_ref=settings.lite_transcribe_engine_ref,
            execution_profile="venv",
        )
    else:
        transcribe = _make_stage_binding(
            stage="transcribe",
            runtime="lite-transcribe",
            compute=_compute_transcribe,
        )

    return {
        "transcribe": transcribe,
        "diarize": _make_stage_binding(
            stage="diarize",
            runtime="lite-diarize",
            compute=_compute_diarize,
        ),
        "pii_detect": _make_stage_binding(
            stage="pii_detect",
            runtime="lite-pii-detect",
            compute=_compute_pii_detect,
        ),
    }


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
        artifacts: ArtifactStore,
        *,
        profile: str = DEFAULT_PROFILE,
        persist_artifacts: bool = True,
        ephemeral_mode: bool = False,
        stage_bindings: dict[str, _LiteStageBinding] | None = None,
        executors: dict[str, RuntimeExecutor] | None = None,
    ) -> None:
        cap = resolve_profile(profile)

        if cap.requires_prereqs:
            missing = check_prerequisites(cap.profile)
            if missing:
                raise LitePrerequisiteMissingError(cap.profile, missing)

        settings = get_settings()
        self._queue = InMemoryQueue()
        self._artifacts = artifacts
        self._persist_artifacts = persist_artifacts
        self._ephemeral_mode = ephemeral_mode
        self._profile = cap.profile
        self._profile_cap = cap
        self._stage_outputs: dict[str, dict[str, dict[str, Any]]] = {}
        self._stage_bindings = dict(
            _build_default_stage_bindings(settings)
            if stage_bindings is None
            else stage_bindings
        )
        if executors is None:
            lite_output_dir = Path(settings.lite_artifacts_dir)
            lite_venv_python = Path(
                settings.lite_venv_python or sys.executable
            ).expanduser()
            venv_runtimes = {
                binding.entry.runtime: lite_venv_python
                for binding in self._stage_bindings.values()
                if binding.entry.execution_profile == "venv"
            }
            self._executors: dict[str, RuntimeExecutor] = {}
            self._executor_factories: dict[str, Callable[[], RuntimeExecutor]] = {
                "inproc": lambda: InProcExecutor(output_dir=lite_output_dir),
                "venv": lambda: VenvExecutor(
                    env_manager=VenvEnvironmentManager(
                        runtime_pythons=venv_runtimes,
                    ),
                    output_dir=lite_output_dir,
                ),
            }
        else:
            self._executors = dict(executors)
            self._executor_factories = {}
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
            Dict with ``job_id`` and final transcript payload metadata. For
            persistent mode this includes ``transcript_uri``; for ephemeral mode
            ``transcript`` is returned inline.

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
        self._stage_outputs[job_id] = {}

        transcribe_audio_path: Path | None = None
        temp_audio_path: Path | None = None
        if self._persist_artifacts:
            audio_uri = await self._artifacts.write_bytes(
                f"jobs/{job_id}/audio/original.wav", audio_bytes
            )
            transcribe_audio_path = self._artifact_uri_to_path(audio_uri)

        if transcribe_audio_path is None:
            with tempfile.NamedTemporaryFile(
                prefix="dalston-lite-audio-",
                suffix=".wav",
                delete=False,
            ) as handle:
                handle.write(audio_bytes)
                temp_audio_path = Path(handle.name)
            transcribe_audio_path = temp_audio_path

        logger.info(
            "lite_job_started",
            job_id=job_id,
            profile=self._profile.value,
        )

        try:
            result = await self._run_loop(
                job_id,
                parameters,
                audio_bytes,
                transcribe_audio_path,
            )
        finally:
            self._stage_outputs.pop(job_id, None)
            if temp_audio_path is not None:
                temp_audio_path.unlink(missing_ok=True)

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

    async def _run_loop(
        self,
        job_id: str,
        parameters: dict,
        audio_bytes: bytes,
        transcribe_audio_path: Path,
    ) -> dict:
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
                    result = await self._handle_stage(
                        stage,
                        envelope,
                        parameters,
                        audio_bytes,
                        transcribe_audio_path,
                    )
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    if result is not None:
                        return result
                await asyncio.sleep(0.01)

    async def _handle_stage(
        self,
        stage: str,
        envelope: QueueEnvelope,
        parameters: dict,
        audio_bytes: bytes,
        transcribe_audio_path: Path,
    ) -> dict | None:
        """Process one stage envelope.

        Returns the final result dict when ``merge`` completes; ``None`` for
        all other stages so the loop continues.
        """
        job_id = envelope.job_id

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
            payload = await self._execute_stage(
                stage,
                envelope,
                parameters,
                audio_bytes,
                transcribe_audio_path,
            )
            self._record_stage_output(job_id, stage, payload)
            if self._persist_artifacts:
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
            diarize_payload = await self._execute_stage(
                stage,
                envelope,
                parameters,
                audio_bytes,
                transcribe_audio_path,
            )
            self._record_stage_output(job_id, stage, diarize_payload)
            if self._persist_artifacts:
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
            pii_payload = await self._execute_stage(
                stage,
                envelope,
                parameters,
                audio_bytes,
                transcribe_audio_path,
            )
            self._record_stage_output(job_id, stage, pii_payload)
            if self._persist_artifacts:
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
        return await self._handle_merge(job_id)

    async def _execute_stage(
        self,
        stage: str,
        envelope: QueueEnvelope,
        parameters: dict,
        audio_bytes: bytes,
        transcribe_audio_path: Path,
    ) -> dict[str, Any]:
        binding = self._stage_bindings.get(stage)
        if binding is None:
            raise RuntimeError(
                f"No lite runtime binding configured for stage '{stage}'"
            )

        previous_outputs = dict(self._stage_outputs.get(envelope.job_id, {}))
        if (
            self._ephemeral_mode
            and binding.entry.execution_profile == "inproc"
            and binding.engine_factory is not None
        ):
            engine = binding.engine_factory()
            engine_input = EngineInput(
                task_id=envelope.task_id,
                job_id=envelope.job_id,
                stage=stage,
                config=parameters,
                payload=audio_bytes,
                previous_outputs=previous_outputs,
                audio_path=transcribe_audio_path if stage == "transcribe" else None,
                materialized_artifacts={},
            )
            ctx = BatchTaskContext(
                runtime=binding.entry.runtime,
                instance=f"lite-{self._profile.value}",
                task_id=envelope.task_id,
                job_id=envelope.job_id,
                stage=stage,
                metadata={
                    "mode": "lite",
                    "execution_profile": binding.entry.execution_profile,
                    "artifact_persistence": "ephemeral",
                },
            )
            output = await asyncio.to_thread(engine.process, engine_input, ctx)
            return output.to_dict()

        executor = self._resolve_executor(binding.entry.execution_profile)
        if executor is None:
            raise RuntimeError(
                "No executor configured for "
                f"profile '{binding.entry.execution_profile}' "
                f"(runtime '{binding.entry.runtime}', stage '{stage}')"
            )

        artifacts: dict[str, Path] = {}
        if stage == "transcribe":
            artifacts["audio"] = transcribe_audio_path

        request = ExecutionRequest(
            task_id=envelope.task_id,
            job_id=envelope.job_id,
            stage=stage,
            runtime=binding.entry.runtime,
            instance=f"lite-{self._profile.value}",
            config=parameters,
            previous_outputs=previous_outputs,
            payload=None,
            artifacts=artifacts,
            engine=binding.engine_factory() if binding.engine_factory else None,
            engine_ref=binding.engine_ref,
            metadata={
                "mode": "lite",
                "execution_profile": binding.entry.execution_profile,
            },
        )

        result = await asyncio.to_thread(executor.execute, request)
        return result["data"]

    def _resolve_executor(self, profile: str) -> RuntimeExecutor | None:
        executor = self._executors.get(profile)
        if executor is not None:
            return executor

        factory = self._executor_factories.get(profile)
        if factory is None:
            return None

        executor = factory()
        self._executors[profile] = executor
        return executor

    @staticmethod
    def _artifact_uri_to_path(uri: str | None) -> Path | None:
        if uri is None or not uri.startswith("file://"):
            return None
        return Path(uri.removeprefix("file://"))

    def _record_stage_output(
        self, job_id: str, stage: str, payload: dict[str, Any]
    ) -> None:
        self._stage_outputs.setdefault(job_id, {})[stage] = payload

    @staticmethod
    def _normalize_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("segments")
        if not isinstance(raw, list):
            return []
        return [segment for segment in raw if isinstance(segment, dict)]

    @staticmethod
    def _resolve_text(payload: dict[str, Any], segments: list[dict[str, Any]]) -> str:
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text
        derived = " ".join(
            segment.get("text", "").strip()
            for segment in segments
            if isinstance(segment.get("text"), str)
        ).strip()
        return derived

    async def _handle_merge(self, job_id: str) -> dict:
        """Assemble final transcript and return result metadata."""
        stage_outputs = self._stage_outputs.get(job_id, {})
        transcribe_payload = stage_outputs.get("transcribe")
        if not isinstance(transcribe_payload, dict):
            raise RuntimeError("Missing transcribe output for lite merge")

        segments = self._normalize_segments(transcribe_payload)
        if self._profile == LiteProfile.SPEAKER:
            transcript: dict = {
                "job_id": job_id,
                "status": "completed",
                "text": self._resolve_text(transcribe_payload, segments),
                "profile": LiteProfile.SPEAKER.value,
                "segments": segments,
            }
            diarize_payload = stage_outputs.get("diarize")
            if isinstance(diarize_payload, dict):
                diarize_segments = self._normalize_segments(diarize_payload)
                if diarize_segments:
                    transcript["segments"] = diarize_segments
                speakers = diarize_payload.get("speakers")
                if isinstance(speakers, list):
                    transcript["speakers"] = [
                        speaker for speaker in speakers if isinstance(speaker, str)
                    ]
        elif self._profile == LiteProfile.COMPLIANCE:
            transcript = {
                "job_id": job_id,
                "status": "completed",
                "text": self._resolve_text(transcribe_payload, segments),
                "profile": LiteProfile.COMPLIANCE.value,
                "segments": segments,
            }
            pii_payload = stage_outputs.get("pii_detect")
            entities: list[Any] = []
            if isinstance(pii_payload, dict):
                raw_entities = pii_payload.get("entities")
                if isinstance(raw_entities, list):
                    entities = raw_entities
                anonymized_text = pii_payload.get("anonymized_text")
                if isinstance(anonymized_text, str) and anonymized_text.strip():
                    transcript["text"] = anonymized_text
            transcript["pii_entities"] = entities
        else:  # core
            transcript = {
                "job_id": job_id,
                "status": "completed",
                "text": self._resolve_text(transcribe_payload, segments),
                "profile": LiteProfile.CORE.value,
                "segments": segments,
            }

        output_uri: str | None = None
        if self._persist_artifacts:
            output_uri = await self._artifacts.write_bytes(
                f"jobs/{job_id}/transcript.json",
                json.dumps(transcript).encode("utf-8"),
                content_type="application/json",
            )
        return {
            "job_id": job_id,
            "transcript_uri": output_uri,
            "transcript": transcript,
        }


# ---------------------------------------------------------------------------
# Public factory functions
# ---------------------------------------------------------------------------


def build_pipeline(
    profile: str = DEFAULT_PROFILE,
    *,
    retention_days: int | None = None,
) -> LitePipeline:
    """Build a ``LitePipeline`` for *profile*.

    Args:
        profile: Profile name (``"core"``, ``"speaker"``, ``"compliance"``).
        retention_days: Retention policy in days. ``0`` enables ephemeral
            in-memory artifacts, ``-1``/``N`` persist to local filesystem. If
            omitted, defaults to ``settings.retention_default_days``.

    Raises:
        RuntimeError: If called outside DALSTON_MODE=lite.
        LiteProfileNotFoundError: If *profile* is not a known profile name.
        LitePrerequisiteMissingError: If the profile's prerequisites are absent.
    """
    settings = get_settings()
    if settings.runtime_mode != "lite":
        raise RuntimeError(LiteMsg.LITE_MODE_REQUIRED)

    effective_retention = (
        settings.retention_default_days if retention_days is None else retention_days
    )
    ephemeral = effective_retention == 0

    artifacts: ArtifactStore
    if ephemeral:
        artifacts = InMemoryArtifactStoreAdapter()
    else:
        artifacts = LocalFilesystemArtifactStoreAdapter(settings.lite_artifacts_dir)

    return LitePipeline(
        artifacts,
        profile=profile,
        persist_artifacts=not ephemeral,
        ephemeral_mode=ephemeral,
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
        raise RuntimeError(LiteMsg.LITE_MAIN_MODE_REQUIRED)
    # Deferred to avoid a circular import: dalston.db.session imports Settings
    # indirectly via dalston.config, which is not fully initialised at the
    # time this module is first imported.
    from dalston.db.session import init_db

    await init_db()


def main() -> None:
    asyncio.run(orchestrator_loop())
