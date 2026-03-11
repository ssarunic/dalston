"""Local filesystem runner for executing batch engines without Redis/S3."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.engine_loader import load_engine
from dalston.engine_sdk.executors import ExecutionRequest, InProcExecutor


class LocalRunner:
    """Executes a single engine task with local filesystem artifact transport."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._executor = InProcExecutor(output_dir=self.output_dir)

    def run(
        self,
        *,
        engine: Engine[Any, Any],
        task_id: str,
        job_id: str,
        stage: str,
        config: dict[str, Any],
        previous_outputs: dict[str, Any],
        payload: dict[str, Any] | None,
        artifacts: dict[str, Path],
    ) -> dict[str, Any]:
        return self._executor.execute(
            ExecutionRequest(
                engine=engine,
                task_id=task_id,
                job_id=job_id,
                stage=stage,
                engine_id="local",
                instance="local-runner",
                config=config,
                previous_outputs=previous_outputs,
                payload=payload,
                artifacts=artifacts,
                metadata={"mode": "local"},
            )
        )


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} file contains invalid JSON at line {exc.lineno} column "
            f"{exc.colno}: {path}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{label} file must contain a JSON object: {path}")

    return parsed


def _load_artifacts(path: Path | None) -> dict[str, Path]:
    if path is None:
        return {}

    artifacts_raw = _load_json_object(path, label="artifacts")
    artifacts: dict[str, Path] = {}
    for slot, locator in artifacts_raw.items():
        if not isinstance(locator, str):
            raise ValueError(
                f"Artifacts values must be file paths encoded as strings: slot={slot}"
            )
        artifact_path = Path(locator)
        if not artifact_path.exists() or not artifact_path.is_file():
            raise FileNotFoundError(
                f"Artifact path for slot '{slot}' does not exist: {artifact_path}"
            )
        artifacts[str(slot)] = artifact_path
    return artifacts


def _load_engine(engine_ref: str) -> Engine[Any, Any]:
    """Backward-compatible alias for existing local runner imports."""
    return load_engine(engine_ref)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dalston.engine_sdk.local_runner",
        description="Run a batch engine locally with file-based inputs only.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run one engine task locally and write canonical output.json",
    )
    run_parser.add_argument(
        "--engine",
        required=True,
        help="Engine class reference in <module:Class> format.",
    )
    run_parser.add_argument("--stage", required=True, help="Pipeline stage name.")
    run_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to required config JSON file.",
    )
    run_parser.add_argument(
        "--audio",
        type=Path,
        help="Optional audio file path for simple local-run flow.",
    )
    run_parser.add_argument(
        "--payload",
        type=Path,
        help="Optional payload JSON file.",
    )
    run_parser.add_argument(
        "--previous-outputs",
        type=Path,
        dest="previous_outputs",
        help="Optional previous outputs JSON file.",
    )
    run_parser.add_argument(
        "--artifacts",
        type=Path,
        help="Optional artifacts JSON file mapping slot to local file path.",
    )
    run_parser.add_argument(
        "--task-id",
        default="task-local",
        help="Optional task ID (default: task-local).",
    )
    run_parser.add_argument(
        "--job-id",
        default="job-local",
        help="Optional job ID (default: job-local).",
    )
    run_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to output.json file to write.",
    )

    return parser


def _run_command(args: argparse.Namespace) -> int:
    config = _load_json_object(args.config, label="config")
    payload = _load_json_object(args.payload, label="payload") if args.payload else None
    previous_outputs = (
        _load_json_object(args.previous_outputs, label="previous_outputs")
        if args.previous_outputs
        else {}
    )

    artifacts = _load_artifacts(args.artifacts)
    if args.audio is not None:
        if "audio" in artifacts:
            raise ValueError(
                "Provide audio via either --audio or artifacts JSON slot 'audio', not "
                "both."
            )
        if not args.audio.exists() or not args.audio.is_file():
            raise FileNotFoundError(f"Audio file not found: {args.audio}")
        artifacts["audio"] = args.audio

    engine = load_engine(args.engine)
    runner = LocalRunner(output_dir=args.output.parent)
    result = runner.run(
        engine=engine,
        task_id=args.task_id,
        job_id=args.job_id,
        stage=args.stage,
        config=config,
        previous_outputs=previous_outputs,
        payload=payload,
        artifacts=artifacts,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{json.dumps(result, indent=2)}\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.print_help()
        return 1

    try:
        return _run_command(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
