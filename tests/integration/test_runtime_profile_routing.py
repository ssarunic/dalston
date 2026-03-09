from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dalston.engine_sdk.executors import VenvEnvironmentManager, VenvExecutor
from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter
from dalston.orchestrator.catalog import EngineCatalog
from dalston.orchestrator.lite_main import LitePipeline, _LiteStageBinding


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_python() -> Path:
    return _repo_root() / ".venv" / "bin" / "python"


def _write_nemo_stub_engine(tmp_path: Path) -> Path:
    engine_path = tmp_path / "nemo_msdd_stub.py"
    engine_path.write_text(
        """
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput, EngineOutput


class NemoMsddStubEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        return EngineOutput(
            data={
                "segments": [
                    {
                        "text": "stub diarization",
                        "speaker": "SPEAKER_00",
                        "start": 0.0,
                        "end": 1.0,
                    }
                ],
                "speakers": ["SPEAKER_00"],
                "runtime": ctx.runtime,
            }
        )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return engine_path


def test_generated_catalog_contains_migrated_execution_profiles() -> None:
    catalog = EngineCatalog.load()

    assert catalog.get_engine("audio-prepare").execution_profile == "container"
    assert catalog.get_engine("final-merger").execution_profile == "container"
    assert catalog.get_engine("nemo-msdd").execution_profile == "venv"
    assert catalog.get_engine("faster-whisper").execution_profile == "container"


@pytest.mark.asyncio
async def test_nemo_msdd_routes_through_venv_profile_from_catalog(
    tmp_path: Path,
) -> None:
    catalog = EngineCatalog.load()
    nemo_entry = catalog.get_engine("nemo-msdd")
    assert nemo_entry is not None
    assert nemo_entry.execution_profile == "venv"

    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    engine_path = _write_nemo_stub_engine(tmp_path)
    executor = VenvExecutor(
        env_manager=VenvEnvironmentManager(
            runtime_pythons={"nemo-msdd": _runtime_python()},
        ),
        output_dir=tmp_path / "artifacts",
        workspace_dir=_repo_root(),
    )
    pipeline = LitePipeline(
        artifacts,
        profile="speaker",
        stage_bindings={
            "diarize": _LiteStageBinding(
                entry=nemo_entry,
                engine_ref=f"{engine_path}:NemoMsddStubEngine",
            )
        },
        executors={"venv": executor},
    )

    envelope = SimpleNamespace(task_id="task-1", job_id="job-1", message_id="msg-1")
    await pipeline._handle_stage(
        "diarize",
        envelope,
        {"speaker_detection": "diarize", "runtime_model_id": "nemo-msdd-test"},
        b"audio",
    )

    output_path = (
        tmp_path / "artifacts" / "jobs" / "job-1" / "tasks" / "diarize" / "output.json"
    )
    data = json.loads(output_path.read_text())
    assert data["runtime"] == "nemo-msdd"
    assert data["speakers"] == ["SPEAKER_00"]
