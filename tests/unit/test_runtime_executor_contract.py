from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.executors import (
    ExecutionRequest,
    InProcExecutor,
    RuntimeExecutor,
)
from dalston.engine_sdk.local_runner import LocalRunner
from dalston.engine_sdk.types import EngineCapabilities, EngineInput, EngineOutput
from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter
from dalston.orchestrator.catalog import CatalogEntry
from dalston.orchestrator.lite_main import (
    LitePipeline,
    _LiteStageBinding,
    build_default_pipeline,
)


class _EchoEngine(Engine):
    def process(
        self,
        input: EngineInput,
        ctx: BatchTaskContext,
    ) -> EngineOutput:
        return EngineOutput(
            data={
                "stage": input.stage,
                "runtime": ctx.runtime,
                "metadata": ctx.metadata,
                "config": input.config,
            }
        )


class _RecordingExecutor(RuntimeExecutor):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "task_id": request.task_id,
            "job_id": request.job_id,
            "stage": request.stage,
            "data": self.payload,
            "produced_artifacts": [],
            "produced_artifact_ids": [],
        }


def _binding(stage: str, execution_profile: str) -> _LiteStageBinding:
    return _LiteStageBinding(
        entry=CatalogEntry(
            runtime=f"{stage}-{execution_profile}",
            image="dalston/test:latest",
            capabilities=EngineCapabilities(
                runtime=f"{stage}-{execution_profile}",
                version="test",
                stages=[stage],
            ),
            execution_profile=execution_profile,
        ),
        engine_factory=_EchoEngine,
    )


def test_inproc_executor_matches_local_runner_contract(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    runner = LocalRunner(output_dir=tmp_path / "runner-out")
    executor = InProcExecutor(output_dir=tmp_path / "executor-out")

    runner_result = runner.run(
        engine=_EchoEngine(),
        task_id="task-local",
        job_id="job-local",
        stage="transcribe",
        config={"model": "tiny"},
        previous_outputs={},
        payload={"x": 1},
        artifacts={"audio": audio},
    )
    executor_result = executor.execute(
        ExecutionRequest(
            engine=_EchoEngine(),
            task_id="task-local",
            job_id="job-local",
            stage="transcribe",
            runtime="local",
            instance="local-runner",
            config={"model": "tiny"},
            previous_outputs={},
            payload={"x": 1},
            artifacts={"audio": audio},
            metadata={"mode": "local"},
        )
    )

    assert executor_result == runner_result


@pytest.mark.asyncio
async def test_lite_pipeline_selects_executor_from_execution_profile(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    executor = _RecordingExecutor(
        payload={
            "text": "executor transcript",
            "segments": [{"text": "executor transcript"}],
        }
    )
    pipeline = LitePipeline(
        artifacts,
        profile="core",
        stage_bindings={"transcribe": _binding("transcribe", "inproc")},
        executors={"inproc": executor},
    )

    envelope = SimpleNamespace(task_id="task-1", job_id="job-1", message_id="msg-1")
    await pipeline._handle_stage(
        "transcribe",
        envelope,
        {"language": "en"},
        b"audio",
        audio,
    )

    assert len(executor.requests) == 1
    request = executor.requests[0]
    assert request.runtime == "transcribe-inproc"
    assert request.metadata["execution_profile"] == "inproc"
    output_path = (
        tmp_path
        / "artifacts"
        / "jobs"
        / "job-1"
        / "tasks"
        / "transcribe"
        / "output.json"
    )
    assert json.loads(output_path.read_text())["text"] == "executor transcript"


@pytest.mark.asyncio
async def test_lite_pipeline_raises_when_profile_executor_is_missing(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    pipeline = LitePipeline(
        artifacts,
        profile="core",
        stage_bindings={"transcribe": _binding("transcribe", "venv")},
        executors={"inproc": _RecordingExecutor(payload={"text": "unused"})},
    )

    envelope = SimpleNamespace(task_id="task-1", job_id="job-1", message_id="msg-1")
    with pytest.raises(RuntimeError, match="No executor configured for profile 'venv'"):
        await pipeline._handle_stage(
            "transcribe",
            envelope,
            {"language": "en"},
            b"audio",
            audio,
        )


@pytest.mark.asyncio
async def test_lite_pipeline_initializes_only_requested_profile_executor(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    pipeline = LitePipeline(
        artifacts,
        profile="core",
        stage_bindings={"transcribe": _binding("transcribe", "inproc")},
    )

    assert pipeline._executors == {}

    envelope = SimpleNamespace(task_id="task-1", job_id="job-1", message_id="msg-1")
    await pipeline._handle_stage(
        "transcribe",
        envelope,
        {"language": "en"},
        b"audio",
        audio,
    )

    assert "inproc" in pipeline._executors
    assert "venv" not in pipeline._executors


@pytest.mark.asyncio
async def test_lite_diarize_stage_injects_default_runtime_model_and_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DIARIZE_RUNTIME_MODEL_ID",
        "nvidia/diar-msdd-telephonic",
    )

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    executor = _RecordingExecutor(payload={"speakers": ["SPEAKER_00"], "turns": []})
    pipeline = LitePipeline(
        artifacts,
        profile="speaker",
        stage_bindings={"diarize": _binding("diarize", "venv")},
        executors={"venv": executor},
    )

    envelope = SimpleNamespace(task_id="task-1", job_id="job-1", message_id="msg-1")
    await pipeline._handle_stage(
        "diarize",
        envelope,
        {"speaker_detection": "diarize", "num_speakers": 3},
        b"audio",
        audio,
    )

    request = executor.requests[0]
    assert request.config["runtime_model_id"] == "nvidia/diar-msdd-telephonic"
    assert request.config["max_speakers"] == 3
    assert request.artifacts["audio"] == audio


def test_default_lite_pipeline_uses_real_transcribe_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_TRANSCRIBE_BACKEND", "real")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    pipeline = build_default_pipeline()
    binding = pipeline._stage_bindings["transcribe"]

    assert binding.entry.execution_profile == "venv"
    assert binding.entry.runtime == "faster-whisper"
    assert binding.engine_ref is not None
    assert binding.engine_ref.endswith("engine.py:FasterWhisperBatchEngine")


def test_default_lite_pipeline_uses_real_diarize_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_DIARIZE_BACKEND", "real")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    pipeline = build_default_pipeline()
    binding = pipeline._stage_bindings["diarize"]

    assert binding.entry.execution_profile == "venv"
    assert binding.entry.runtime == "nemo-msdd"
    assert binding.engine_ref is not None
    assert binding.engine_ref.endswith("engine.py:NemoMSDDEngine")
