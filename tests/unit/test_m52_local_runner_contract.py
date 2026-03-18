"""M52 contract tests for local runner behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from dalston.engine_sdk import local_runner
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.local_runner import LocalRunner
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class ContractEngine(Engine):
    def process(
        self,
        input: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        del input
        del ctx
        return TaskResponse(data={"ok": True})


def test_local_runner_no_longer_writes_merge_transcript_sidecar(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    runner = LocalRunner(output_dir=tmp_path / "out")
    runner.run(
        engine=ContractEngine(),
        task_id="task-local",
        job_id="job-local",
        stage="merge",
        config={},
        previous_responses={},
        payload={},
        artifacts={"audio": audio},
    )

    transcript_sidecar = tmp_path / "out" / "jobs" / "job-local" / "transcript.json"
    assert not transcript_sidecar.exists()


def test_local_runner_output_envelope_is_deterministic(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    runner = LocalRunner(output_dir=tmp_path / "out")
    result_1 = runner.run(
        engine=ContractEngine(),
        task_id="task-local",
        job_id="job-local",
        stage="transcribe",
        config={"model": "tiny"},
        previous_responses={},
        payload={},
        artifacts={"audio": audio},
    )
    result_2 = runner.run(
        engine=ContractEngine(),
        task_id="task-local",
        job_id="job-local",
        stage="transcribe",
        config={"model": "tiny"},
        previous_responses={},
        payload={},
        artifacts={"audio": audio},
    )

    assert result_1 == result_2


def test_cli_rejects_duplicate_audio_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    artifacts = tmp_path / "artifacts.json"
    artifacts.write_text(f'{{"audio": "{audio}"}}', encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            f"{__name__}:ContractEngine",
            "--stage",
            "transcribe",
            "--audio",
            str(audio),
            "--artifacts",
            str(artifacts),
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    assert "either --audio or artifacts JSON slot 'audio'" in capsys.readouterr().err
