from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dalston.engine_sdk.executors import (
    ExecutionRequest,
    VenvEnvironmentManager,
    VenvExecutor,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_python() -> Path:
    return _repo_root() / ".venv" / "bin" / "python"


def _write_engine_module(tmp_path: Path) -> Path:
    engine_path = tmp_path / "venv_echo_engine.py"
    engine_path.write_text(
        """
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput, EngineOutput


class VenvEchoEngine(Engine):
    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        return EngineOutput(
            data={
                "config_value": input.config["value"],
                "payload_value": input.payload["value"],
                "audio_exists": input.audio_path.exists(),
                "runtime": ctx.runtime,
            }
        )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return engine_path


def test_env_manager_resolves_and_caches_runtime_python(tmp_path: Path) -> None:
    manager = VenvEnvironmentManager(
        runtime_pythons={"stub-runtime": _runtime_python()},
    )

    environment_1 = manager.ensure_environment("stub-runtime")
    environment_2 = manager.ensure_environment("stub-runtime")

    assert environment_1.python_executable == _runtime_python().absolute()
    assert environment_1 is environment_2


def test_venv_executor_runs_serialized_request(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    engine_path = _write_engine_module(tmp_path)

    manager = VenvEnvironmentManager(
        runtime_pythons={"stub-runtime": _runtime_python()},
    )
    executor = VenvExecutor(
        env_manager=manager,
        output_dir=tmp_path / "artifacts",
        workspace_dir=_repo_root(),
    )

    result = executor.execute(
        ExecutionRequest(
            task_id="task-1",
            job_id="job-1",
            stage="transcribe",
            runtime="stub-runtime",
            instance="lite-test",
            config={"value": 7},
            previous_outputs={},
            payload={"value": 9},
            artifacts={"audio": audio},
            engine_ref=f"{engine_path}:VenvEchoEngine",
            metadata={"execution_profile": "venv"},
        )
    )

    assert result["data"] == {
        "config_value": 7,
        "payload_value": 9,
        "audio_exists": True,
        "runtime": "stub-runtime",
    }


def test_venv_executor_requires_engine_ref(tmp_path: Path) -> None:
    manager = VenvEnvironmentManager(
        runtime_pythons={"stub-runtime": _runtime_python()},
    )
    executor = VenvExecutor(
        env_manager=manager,
        output_dir=tmp_path / "artifacts",
        workspace_dir=_repo_root(),
    )

    with pytest.raises(ValueError, match="engine_ref"):
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                job_id="job-1",
                stage="transcribe",
                runtime="stub-runtime",
                instance="lite-test",
                config={},
                previous_outputs={},
                payload=None,
                artifacts={},
            )
        )


def test_env_manager_health_check_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = VenvEnvironmentManager(
        runtime_pythons={"stub-runtime": _runtime_python()},
        health_check_timeout_s=1,
    )

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(
        "dalston.engine_sdk.executors.env_manager.subprocess.run",
        _timeout,
    )

    with pytest.raises(RuntimeError, match="Health check timed out"):
        manager.ensure_environment("stub-runtime")


def test_venv_executor_subprocess_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = VenvEnvironmentManager(
        runtime_pythons={"stub-runtime": _runtime_python()},
    )
    executor = VenvExecutor(
        env_manager=manager,
        output_dir=tmp_path / "artifacts",
        workspace_dir=_repo_root(),
        subprocess_timeout_s=1,
    )
    # Warm cache so this test exercises only the executor subprocess timeout.
    manager.ensure_environment("stub-runtime")

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(
        "dalston.engine_sdk.executors.venv_executor.subprocess.run",
        _timeout,
    )

    with pytest.raises(RuntimeError, match="Venv executor timed out"):
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                job_id="job-1",
                stage="transcribe",
                runtime="stub-runtime",
                instance="lite-test",
                config={},
                previous_outputs={},
                payload=None,
                artifacts={},
                engine_ref="engines.fake:FakeEngine",
            )
        )
