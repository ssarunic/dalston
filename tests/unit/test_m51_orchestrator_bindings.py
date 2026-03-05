"""Phase-2 tests for orchestrator artifact binding resolution."""

from __future__ import annotations

from uuid import uuid4

from dalston.common.artifacts import ArtifactReference, InputBinding
from dalston.orchestrator.scheduler import _resolve_input_bindings
from tests.dag_test_helpers import build_task_dag_for_test


def test_transcribe_task_declares_prepare_audio_binding() -> None:
    tasks = build_task_dag_for_test(
        job_id=uuid4(),
        audio_uri="s3://bucket/input.wav",
        parameters={},
    )
    transcribe = next(task for task in tasks if task.stage == "transcribe")

    assert transcribe.input_bindings == [
        {
            "slot": "audio",
            "selector": {
                "producer_stage": "prepare",
                "kind": "audio",
                "role": "prepared",
                "required": True,
            },
        }
    ]


def test_per_channel_binding_declares_channel_selector() -> None:
    tasks = build_task_dag_for_test(
        job_id=uuid4(),
        audio_uri="s3://bucket/stereo.wav",
        parameters={"speaker_detection": "per_channel"},
    )
    transcribe_ch1 = next(task for task in tasks if task.stage == "transcribe_ch1")

    assert transcribe_ch1.input_bindings[0]["selector"]["channel"] == 1


def test_binding_resolution_prefers_selector_channel_role() -> None:
    artifact_index = {
        "prepare_ch0": ArtifactReference(
            artifact_id="prepare_ch0",
            kind="audio",
            storage_locator="s3://bucket/jobs/j1/ch0.wav",
            producer_stage="prepare",
            channel=0,
            role="prepared",
        ),
        "prepare_ch1": ArtifactReference(
            artifact_id="prepare_ch1",
            kind="audio",
            storage_locator="s3://bucket/jobs/j1/ch1.wav",
            producer_stage="prepare",
            channel=1,
            role="prepared",
        ),
    }

    resolved = _resolve_input_bindings(
        bindings=[
            InputBinding.model_validate(
                {
                    "slot": "audio",
                    "selector": {
                        "producer_stage": "prepare",
                        "kind": "audio",
                        "channel": 1,
                        "role": "prepared",
                        "required": True,
                    },
                }
            )
        ],
        artifact_index=artifact_index,
    )

    assert resolved == {"audio": "prepare_ch1"}
