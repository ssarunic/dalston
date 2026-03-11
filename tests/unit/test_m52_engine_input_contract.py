"""M52 tests for EngineInput contract hardening."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dalston.common.artifacts import MaterializedArtifact
from dalston.engine_sdk.types import EngineInput


def test_audio_path_stays_none_without_audio_or_artifacts() -> None:
    input_data = EngineInput(task_id="task-1", job_id="job-1")
    assert input_data.audio_path is None


def test_audio_path_derived_from_audio_artifact_slot() -> None:
    artifact = MaterializedArtifact(
        artifact_id="task-1:audio",
        kind="audio",
        local_path=Path("/tmp/audio.wav"),
        role="audio",
    )
    input_data = EngineInput(
        task_id="task-1",
        job_id="job-1",
        materialized_artifacts={"audio": artifact},
    )
    assert input_data.audio_path == Path("/tmp/audio.wav")


def test_typed_output_raises_on_invalid_structure() -> None:
    input_data = EngineInput(
        task_id="task-1",
        job_id="job-1",
        previous_outputs={"transcribe": {"segments": "invalid-shape"}},
    )
    with pytest.raises(ValidationError):
        input_data.get_transcript()


def test_typed_output_returns_none_when_key_missing() -> None:
    input_data = EngineInput(task_id="task-1", job_id="job-1", previous_outputs={})
    assert input_data.get_transcript() is None
