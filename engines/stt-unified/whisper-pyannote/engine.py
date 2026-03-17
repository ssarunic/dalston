"""Combined Faster-Whisper + Pyannote engine.

A composite engine that runs transcription (faster-whisper) and speaker
diarization (pyannote) as child engines.  When both stages are requested
the children run in parallel on the same audio, and results are merged
into a single stage-keyed response.

This is the Layer 2 composite engine from ENGINE_COMPOSABILITY §5.

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID (default: "whisper-pyannote")
    DALSTON_DEFAULT_MODEL_ID: Default transcription model (default: "large-v3-turbo")
    DALSTON_DIARIZE_MODEL_ID: Default diarization model
        (default: "pyannote/speaker-diarization-community-1")
    DALSTON_DEVICE: Device for inference (cuda, cpu). Auto-detected if unset.
    DALSTON_MODEL_TTL_SECONDS: Evict idle models after N seconds (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max loaded whisper models (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    HF_TOKEN: HuggingFace token for pyannote gated models
    DALSTON_DIARIZATION_DISABLED: Set to "true" to skip diarization
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import sys
from pathlib import Path

import structlog

from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

logger = structlog.get_logger()

# Default diarization model
_DEFAULT_DIARIZE_MODEL = "pyannote/speaker-diarization-community-1"


def _ensure_child_paths() -> None:
    """Add child engine directories to sys.path for direct imports."""
    # When running in a container, child engine code is copied to /app/engines/...
    # When running locally, the repo root is the base.
    app_dir = str(Path(__file__).resolve().parents[3])  # repo root
    for child_dir in [
        str(Path(app_dir) / "engines" / "stt-unified" / "faster-whisper"),
        str(Path(app_dir) / "engines" / "stt-diarize" / "pyannote-4.0"),
    ]:
        if child_dir not in sys.path:
            sys.path.insert(0, child_dir)


_ensure_child_paths()


class WhisperPyannoteEngine(Engine):
    """Combined engine orchestrating faster-whisper and pyannote as children.

    The engine holds references to both child engines and dispatches work
    based on the requested stage:

    - ``stage="transcribe"`` → delegates to faster-whisper only
    - ``stage="diarize"`` → delegates to pyannote only
    - ``stage="combined"`` → runs both in parallel, returns merged result
    """

    def __init__(self) -> None:
        super().__init__()

        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "whisper-pyannote")
        self._default_diarize_model = os.environ.get(
            "DALSTON_DIARIZE_MODEL_ID", _DEFAULT_DIARIZE_MODEL
        )

        # Lazily initialised child engines — avoids importing heavy
        # dependencies (torch, faster_whisper, pyannote) until first use.
        self._transcribe_engine: Any | None = None
        self._diarize_engine: Any | None = None

        self.logger.info(
            "combined_engine_init",
            engine_id=self._engine_id,
        )

    # ------------------------------------------------------------------
    # Lazy child engine creation
    # ------------------------------------------------------------------

    def _get_transcribe_engine(self):
        """Return the faster-whisper child engine (created on first call)."""
        if self._transcribe_engine is None:
            from batch_engine import FasterWhisperBatchEngine

            self._transcribe_engine = FasterWhisperBatchEngine()
            self.logger.info("child_engine_created", child="faster-whisper")
        return self._transcribe_engine

    def _get_diarize_engine(self):
        """Return the pyannote child engine (created on first call)."""
        if self._diarize_engine is None:
            from engine import PyannoteEngine as _PyannoteEngine

            self._diarize_engine = _PyannoteEngine()
            self.logger.info("child_engine_created", child="pyannote-4.0")
        return self._diarize_engine

    # ------------------------------------------------------------------
    # Engine interface
    # ------------------------------------------------------------------

    def process(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Dispatch to child engines based on the requested stage."""
        stage = task_request.config.get("_stage", task_request.stage)

        if stage == "transcribe":
            return self._run_transcribe(task_request, ctx)
        if stage == "diarize":
            return self._run_diarize(task_request, ctx)
        if stage == "combined":
            return self._run_combined(task_request, ctx)

        # Fallback: treat unknown stages as combined
        self.logger.warning("unknown_stage_falling_back_to_combined", stage=stage)
        return self._run_combined(task_request, ctx)

    def _run_transcribe(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> TaskResponse:
        """Run transcription only via the faster-whisper child."""
        engine = self._get_transcribe_engine()

        # Strip the internal _stage key before forwarding
        config = {k: v for k, v in task_request.config.items() if k != "_stage"}
        child_request = TaskRequest(
            task_id=task_request.task_id,
            job_id=task_request.job_id,
            stage="transcribe",
            config=config,
            payload=task_request.payload,
            audio_path=task_request.audio_path,
            materialized_artifacts=task_request.materialized_artifacts,
            metadata=task_request.metadata,
        )
        return engine.process(child_request, ctx)

    def _run_diarize(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> TaskResponse:
        """Run diarization only via the pyannote child."""
        engine = self._get_diarize_engine()

        config = {k: v for k, v in task_request.config.items() if k != "_stage"}
        # Set the diarize model if not already set
        if "loaded_model_id" not in config:
            config["loaded_model_id"] = self._default_diarize_model

        child_request = TaskRequest(
            task_id=task_request.task_id,
            job_id=task_request.job_id,
            stage="diarize",
            config=config,
            payload=task_request.payload,
            audio_path=task_request.audio_path,
            materialized_artifacts=task_request.materialized_artifacts,
            metadata=task_request.metadata,
        )
        return engine.process(child_request, ctx)

    def _run_combined(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> TaskResponse:
        """Run transcription and diarization in parallel, merge results.

        Both children receive the same audio file.  The transcription
        child gets transcribe-specific config; the diarization child
        gets diarize-specific config.  Results are merged into a single
        stage-keyed dict.
        """
        self._set_runtime_state(status="processing")

        # Split config into transcribe and diarize parameters
        transcribe_config = self._extract_transcribe_config(task_request.config)
        diarize_config = self._extract_diarize_config(task_request.config)

        transcribe_request = TaskRequest(
            task_id=f"{task_request.task_id}-transcribe",
            job_id=task_request.job_id,
            stage="transcribe",
            config=transcribe_config,
            payload=task_request.payload,
            audio_path=task_request.audio_path,
            materialized_artifacts=task_request.materialized_artifacts,
            metadata=task_request.metadata,
        )

        diarize_request = TaskRequest(
            task_id=f"{task_request.task_id}-diarize",
            job_id=task_request.job_id,
            stage="diarize",
            config=diarize_config,
            payload=task_request.payload,
            audio_path=task_request.audio_path,
            materialized_artifacts=task_request.materialized_artifacts,
            metadata=task_request.metadata,
        )

        transcribe_ctx = BatchTaskContext.for_http(
            task_id=transcribe_request.task_id,
            job_id=task_request.job_id,
            engine_id="faster-whisper",
            stage="transcribe",
        )
        diarize_ctx = BatchTaskContext.for_http(
            task_id=diarize_request.task_id,
            job_id=task_request.job_id,
            engine_id="pyannote-4.0",
            stage="diarize",
        )

        transcribe_engine = self._get_transcribe_engine()
        diarize_engine = self._get_diarize_engine()

        transcribe_result: TaskResponse | None = None
        diarize_result: TaskResponse | None = None
        errors: list[str] = []

        try:
            # Fan out to both children in parallel
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(
                        transcribe_engine.process, transcribe_request, transcribe_ctx
                    ): "transcribe",
                    pool.submit(
                        diarize_engine.process, diarize_request, diarize_ctx
                    ): "diarize",
                }

                for future in as_completed(futures):
                    stage_name = futures[future]
                    try:
                        result = future.result()
                        if stage_name == "transcribe":
                            transcribe_result = result
                        else:
                            diarize_result = result
                        self.logger.info(
                            "child_stage_completed", stage=stage_name
                        )
                    except Exception:
                        self.logger.exception(
                            "child_stage_failed", stage=stage_name
                        )
                        errors.append(stage_name)

            # Merge results into a stage-keyed envelope
            merged = self._merge_results(
                transcribe_result, diarize_result, errors
            )
            return TaskResponse(data=merged)
        finally:
            self._set_runtime_state(status="idle")

    # ------------------------------------------------------------------
    # Config splitting
    # ------------------------------------------------------------------

    def _extract_transcribe_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Extract transcription-specific config keys."""
        out: dict[str, Any] = {}
        for key in (
            "loaded_model_id",
            "language",
            "word_timestamps",
            "vocabulary",
            "channel",
            "beam_size",
            "vad_filter",
            "temperature",
            "task",
            "prompt",
        ):
            if key in config:
                out[key] = config[key]
        return out

    def _extract_diarize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Extract diarization-specific config keys."""
        out: dict[str, Any] = {}

        # The combined endpoint uses diarize_model_id for the diarize child
        diarize_model = config.get("diarize_model_id", self._default_diarize_model)
        out["loaded_model_id"] = diarize_model

        for key in ("num_speakers", "min_speakers", "max_speakers", "exclusive"):
            if key in config:
                out[key] = config[key]
        return out

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _merge_results(
        self,
        transcribe_result: TaskResponse | None,
        diarize_result: TaskResponse | None,
        errors: list[str],
    ) -> dict[str, Any]:
        """Merge child results into a stage-keyed envelope.

        Returns a dict with ``transcription`` and ``diarization`` keys,
        following the ENGINE_COMPOSABILITY §3.3 result format.
        """
        merged: dict[str, Any] = {
            "engine_id": self._engine_id,
            "stages_completed": [],
        }

        if transcribe_result is not None:
            merged["transcription"] = transcribe_result.to_dict()
            merged["stages_completed"].append("transcription")
        elif "transcribe" in errors:
            merged["transcription"] = {
                "skipped": True,
                "skip_reason": "transcription child engine failed",
            }

        if diarize_result is not None:
            merged["diarization"] = diarize_result.to_dict()
            merged["stages_completed"].append("diarization")
        elif "diarize" in errors:
            merged["diarization"] = {
                "skipped": True,
                "skip_reason": "diarization child engine failed",
            }

        if errors:
            merged["warnings"] = [f"Stage failed: {s}" for s in errors]

        return merged

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_http_server(self, port: int = 9100):  # type: ignore[override]
        """Return a ``CombinedHTTPServer`` with all three POST endpoints."""
        from dalston.engine_sdk.http_combined import CombinedHTTPServer

        return CombinedHTTPServer(engine=self, port=port)

    def get_capabilities(self) -> EngineCapabilities:
        """Return capabilities covering both transcription and diarization."""
        return EngineCapabilities(
            engine_id=self._engine_id,
            version="1.0.0",
            stages=["transcribe", "diarize"],
            supports_word_timestamps=True,
            includes_diarization=True,
            supports_native_streaming=False,
            gpu_required=False,
            supports_cpu=True,
            gpu_vram_mb=8 * 1024,  # combined VRAM for both children
            min_ram_gb=12,
        )

    def health_check(self) -> dict[str, Any]:
        """Aggregate health from child engines."""
        health: dict[str, Any] = {
            "status": "healthy",
            "engine_id": self._engine_id,
            "children": {},
        }

        if self._transcribe_engine is not None:
            try:
                health["children"]["faster-whisper"] = (
                    self._transcribe_engine.health_check()
                )
            except Exception as e:
                health["children"]["faster-whisper"] = {
                    "status": "unhealthy",
                    "error": str(e),
                }
                health["status"] = "degraded"

        if self._diarize_engine is not None:
            try:
                health["children"]["pyannote-4.0"] = (
                    self._diarize_engine.health_check()
                )
            except Exception as e:
                health["children"]["pyannote-4.0"] = {
                    "status": "unhealthy",
                    "error": str(e),
                }
                health["status"] = "degraded"

        return health

    def shutdown(self) -> None:
        """Shut down both child engines."""
        if self._transcribe_engine is not None:
            self._transcribe_engine.shutdown()
        if self._diarize_engine is not None:
            self._diarize_engine.shutdown()
        self.logger.info("combined_engine_shutdown")


if __name__ == "__main__":
    engine = WhisperPyannoteEngine()
    engine.run()
