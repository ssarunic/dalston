"""Phase-4 tests for local runner execution without Redis/S3."""

from __future__ import annotations

from pathlib import Path

from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.local_runner import LocalRunner
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class _LocalEchoEngine(Engine):
    def process(
        self,
        input: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        output_path = input.audio_path.parent / "echo.txt"
        output_path.write_text("local-runner-ok", encoding="utf-8")
        return TaskResponse(
            data={"status": "ok", "stage": input.stage},
            produced_artifacts=[
                ctx.describe_artifact(
                    logical_name="echo_output",
                    local_path=output_path,
                    kind="task_output",
                    media_type="text/plain",
                )
            ],
        )


def test_local_runner_executes_engine_and_persists_artifacts(tmp_path: Path) -> None:
    source_audio = tmp_path / "audio.wav"
    source_audio.write_bytes(b"audio")

    runner = LocalRunner(output_dir=tmp_path / "out")
    result = runner.run(
        engine=_LocalEchoEngine(),
        task_id="task-local",
        job_id="job-local",
        stage="merge",
        config={},
        previous_responses={},
        payload={},
        artifacts={"audio": source_audio},
    )

    assert result["data"]["status"] == "ok"
    assert result["produced_artifact_ids"] == ["task-local:echo_output"]
    locator = result["produced_artifacts"][0]["storage_locator"]
    assert Path(locator).exists()
