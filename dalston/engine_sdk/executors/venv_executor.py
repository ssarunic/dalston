"""Subprocess executor for runtime-specific virtualenvs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from dalston.engine_sdk.executors.base import ExecutionRequest, RuntimeExecutor
from dalston.engine_sdk.executors.env_manager import VenvEnvironmentManager
from dalston.engine_sdk.executors.inproc_executor import InProcExecutor


class VenvExecutor(RuntimeExecutor):
    """Execute tasks in a configured virtualenv via subprocess."""

    def __init__(
        self,
        *,
        env_manager: VenvEnvironmentManager,
        output_dir: Path,
        workspace_dir: Path | None = None,
        subprocess_timeout_s: float = 300.0,
    ) -> None:
        if subprocess_timeout_s <= 0:
            raise ValueError("subprocess_timeout_s must be > 0")
        self._env_manager = env_manager
        self._output_dir = Path(output_dir).expanduser().resolve()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_dir = (
            Path(workspace_dir).expanduser().resolve()
            if workspace_dir is not None
            else Path(__file__).resolve().parents[3]
        )
        self._subprocess_timeout_s = subprocess_timeout_s

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        if not request.engine_ref:
            raise ValueError("VenvExecutor requires ExecutionRequest.engine_ref")

        environment = self._env_manager.ensure_environment(request.runtime)

        with tempfile.TemporaryDirectory(prefix="dalston-venv-executor-") as tmp:
            temp_dir = Path(tmp)
            request_path = temp_dir / "request.json"
            output_path = temp_dir / "output.json"
            request_path.write_text(
                json.dumps(self._serialize_request(request), indent=2) + "\n",
                encoding="utf-8",
            )

            try:
                completed = subprocess.run(
                    [
                        str(environment.python_executable),
                        "-m",
                        "dalston.engine_sdk.executors.venv_executor",
                        "--request",
                        str(request_path),
                        "--output",
                        str(output_path),
                    ],
                    cwd=self._workspace_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self._subprocess_timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "Venv executor timed out for runtime "
                    f"'{request.runtime}' after {self._subprocess_timeout_s:.0f}s"
                ) from exc
            if completed.returncode != 0:
                stderr = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(
                    f"Venv executor failed for runtime '{request.runtime}': {stderr}"
                )

            return _load_json_object(output_path)

    def _serialize_request(self, request: ExecutionRequest) -> dict[str, Any]:
        return {
            "engine_ref": request.engine_ref,
            "task_id": request.task_id,
            "job_id": request.job_id,
            "stage": request.stage,
            "runtime": request.runtime,
            "instance": request.instance,
            "config": request.config,
            "previous_outputs": request.previous_outputs,
            "payload": request.payload,
            "artifacts": {slot: str(path) for slot, path in request.artifacts.items()},
            "metadata": request.metadata,
            "output_dir": str(self._output_dir),
        }


def _load_json_object(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return parsed


def _worker_execute(request_path: Path, output_path: Path) -> int:
    from dalston.engine_sdk import local_runner

    request_data = _load_json_object(request_path)
    executor = InProcExecutor(output_dir=Path(request_data["output_dir"]))
    result = executor.execute(
        ExecutionRequest(
            task_id=request_data["task_id"],
            job_id=request_data["job_id"],
            stage=request_data["stage"],
            runtime=request_data["runtime"],
            instance=request_data["instance"],
            config=request_data["config"],
            previous_outputs=request_data["previous_outputs"],
            payload=request_data.get("payload"),
            artifacts={
                slot: Path(locator)
                for slot, locator in request_data.get("artifacts", {}).items()
            },
            engine=local_runner._load_engine(request_data["engine_ref"]),
            engine_ref=request_data["engine_ref"],
            metadata=request_data.get("metadata", {}),
        )
    )
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dalston.engine_sdk.executors.venv_executor",
        description="Run one serialized engine task inside a target virtualenv.",
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        return _worker_execute(args.request, args.output)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
