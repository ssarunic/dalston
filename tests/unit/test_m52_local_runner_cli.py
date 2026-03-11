"""M52 tests for file-based local runner CLI flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dalston.engine_sdk import local_runner
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput, EngineOutput


class CliHappyEngine(Engine):
    def process(
        self,
        input: EngineInput,
        ctx: BatchTaskContext,
    ) -> EngineOutput:
        artifact_path = input.audio_path.parent / "happy.txt"
        artifact_path.write_text("ok", encoding="utf-8")
        return EngineOutput(
            data={"status": "ok", "stage": input.stage},
            produced_artifacts=[
                ctx.describe_artifact(
                    logical_name="happy",
                    local_path=artifact_path,
                    kind="task_output",
                    media_type="text/plain",
                )
            ],
        )


class CliAdvancedEngine(Engine):
    def process(
        self,
        input: EngineInput,
        ctx: BatchTaskContext,
    ) -> EngineOutput:
        del ctx
        return EngineOutput(
            data={
                "payload_flag": bool((input.payload or {}).get("flag")),
                "has_prepare_output": "prepare" in input.previous_outputs,
                "has_hint_artifact": "hint" in input.materialized_artifacts,
            }
        )


def test_cli_run_happy_path_writes_output_json(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    config = tmp_path / "config.json"
    config.write_text('{"model": "tiny"}', encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            f"{__name__}:CliHappyEngine",
            "--stage",
            "transcribe",
            "--audio",
            str(audio),
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert list(envelope) == [
        "task_id",
        "job_id",
        "stage",
        "data",
        "produced_artifacts",
        "produced_artifact_ids",
    ]
    assert envelope["task_id"] == "task-local"
    assert envelope["job_id"] == "job-local"
    assert envelope["stage"] == "transcribe"
    assert envelope["data"] == {"status": "ok", "stage": "transcribe"}
    assert envelope["produced_artifact_ids"] == ["task-local:happy"]


def test_cli_run_supports_advanced_json_inputs(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"loaded_model_id": "aligner-v1"}', encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text('{"flag": true}', encoding="utf-8")
    previous_outputs = tmp_path / "previous_outputs.json"
    previous_outputs.write_text('{"prepare": {"duration": 2.0}}', encoding="utf-8")
    hint = tmp_path / "hint.json"
    hint.write_text('{"notes": "align"}', encoding="utf-8")
    artifacts = tmp_path / "artifacts.json"
    artifacts.write_text(json.dumps({"hint": str(hint)}), encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            f"{__name__}:CliAdvancedEngine",
            "--stage",
            "align",
            "--config",
            str(config),
            "--payload",
            str(payload),
            "--previous-outputs",
            str(previous_outputs),
            "--artifacts",
            str(artifacts),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["stage"] == "align"
    assert envelope["data"] == {
        "payload_flag": True,
        "has_prepare_output": True,
        "has_hint_artifact": True,
    }


def test_cli_run_fails_with_bad_engine_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            "not-a-module-ref",
            "--stage",
            "transcribe",
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    assert "<module:Class>" in capsys.readouterr().err


def test_cli_run_fails_with_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            f"{__name__}:CliHappyEngine",
            "--stage",
            "transcribe",
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_cli_run_loads_engine_from_filesystem_module_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_file = (
        tmp_path / "engines" / "stt-transcribe" / "my-hyphen-engine_id" / "engine.py"
    )
    engine_file.parent.mkdir(parents=True, exist_ok=True)
    engine_file.write_text(
        """
from dalston.engine_sdk import BatchTaskContext, Engine, EngineInput, EngineOutput


class FileRefEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        del input
        del ctx
        return EngineOutput(data={"loaded": True})
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            "engines.stt-transcribe.my-hyphen-engine_id.engine:FileRefEngine",
            "--stage",
            "transcribe",
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["data"] == {"loaded": True}


def test_cli_run_loads_engine_when_engine_id_id_contains_dot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_file = tmp_path / "engines" / "stt-diarize" / "my-engine_id.1" / "engine.py"
    engine_file.parent.mkdir(parents=True, exist_ok=True)
    engine_file.write_text(
        """
from dalston.engine_sdk import BatchTaskContext, Engine, EngineInput, EngineOutput


class DottedRuntimeEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        del input
        del ctx
        return EngineOutput(data={"engine_id": "dot-ok"})
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            "engines.stt-diarize.my-engine_id.1.engine:DottedRuntimeEngine",
            "--stage",
            "diarize",
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["data"] == {"engine_id": "dot-ok"}


def test_cli_run_supports_sibling_module_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_dir = tmp_path / "engines" / "stt-align" / "with-helper"
    engine_dir.mkdir(parents=True, exist_ok=True)

    helper_file = engine_dir / "align.py"
    helper_file.write_text(
        "def helper_value() -> str:\n    return 'sibling-ok'\n",
        encoding="utf-8",
    )

    engine_file = engine_dir / "engine.py"
    engine_file.write_text(
        """
from align import helper_value
from dalston.engine_sdk import BatchTaskContext, Engine, EngineInput, EngineOutput


class SiblingImportEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        del input
        del ctx
        return EngineOutput(data={"helper": helper_value()})
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"

    exit_code = local_runner.main(
        [
            "run",
            "--engine",
            "engines.stt-align.with-helper.engine:SiblingImportEngine",
            "--stage",
            "align",
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["data"] == {"helper": "sibling-ok"}
