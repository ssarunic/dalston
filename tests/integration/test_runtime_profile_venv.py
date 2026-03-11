from __future__ import annotations

import json
from pathlib import Path

import pytest

from dalston.engine_sdk.executors import VenvEnvironmentManager, VenvExecutor
from dalston.engine_sdk.types import EngineCapabilities
from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter
from dalston.orchestrator.catalog import CatalogEntry
from dalston.orchestrator.lite_main import LitePipeline, _LiteStageBinding


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _engine_id_python() -> Path:
    return _repo_root() / ".venv" / "bin" / "python"


def _write_engine_module(tmp_path: Path) -> Path:
    engine_path = tmp_path / "venv_pipeline_engine.py"
    engine_path.write_text(
        """
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput, EngineOutput


class VenvPipelineEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        return EngineOutput(
            data={
                "text": "venv transcript",
                "segments": [{"text": "venv transcript"}],
                "engine_id": ctx.engine_id,
            }
        )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return engine_path


@pytest.mark.asyncio
async def test_lite_pipeline_routes_venv_stage_execution(tmp_path: Path) -> None:
    artifacts = LocalFilesystemArtifactStoreAdapter(str(tmp_path / "artifacts"))
    engine_path = _write_engine_module(tmp_path)
    manager = VenvEnvironmentManager(
        runtime_pythons={"venv-transcribe": _engine_id_python()},
    )
    executor = VenvExecutor(
        env_manager=manager,
        output_dir=tmp_path / "artifacts",
        workspace_dir=_repo_root(),
    )
    binding = _LiteStageBinding(
        entry=CatalogEntry(
            engine_id="venv-transcribe",
            image="dalston/test:latest",
            capabilities=EngineCapabilities(
                engine_id="venv-transcribe",
                version="test",
                stages=["transcribe"],
            ),
            execution_profile="venv",
        ),
        engine_ref=f"{engine_path}:VenvPipelineEngine",
    )
    pipeline = LitePipeline(
        artifacts,
        profile="core",
        stage_bindings={"transcribe": binding},
        executors={"venv": executor},
    )

    result = await pipeline.run_job(b"audio")

    transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
    assert transcript_path.exists()
    transcript_data = json.loads(transcript_path.read_text())
    assert transcript_data["text"] == "venv transcript"
    assert transcript_data["segments"][0]["text"] == "venv transcript"

    transcribe_output = (
        tmp_path
        / "artifacts"
        / "jobs"
        / result["job_id"]
        / "tasks"
        / "transcribe"
        / "output.json"
    )
    transcribe_data = json.loads(transcribe_output.read_text())
    assert transcribe_data["text"] == "venv transcript"
    assert transcribe_data["engine_id"] == "venv-transcribe"
