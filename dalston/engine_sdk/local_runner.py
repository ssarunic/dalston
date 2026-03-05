"""Local filesystem runner for executing batch engines without Redis/S3."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from dalston.common.artifacts import ArtifactReference, ProducedArtifact
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.materializer import (
    ArtifactMaterializer,
    LocalFilesystemArtifactStore,
)
from dalston.engine_sdk.types import EngineInput


class LocalRunner:
    """Executes a single engine task with local filesystem artifact transport."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._store = LocalFilesystemArtifactStore()
        self._materializer = ArtifactMaterializer(
            store=self._store,
            locator_builder=self._local_locator_builder,
        )

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
        with tempfile.TemporaryDirectory(prefix="dalston-local-runner-") as tmp:
            temp_dir = Path(tmp)
            artifact_index: dict[str, ArtifactReference] = {}
            resolved_artifact_ids: dict[str, str] = {}
            for slot, path in artifacts.items():
                artifact_id = f"{job_id}:local:{slot}"
                artifact_index[artifact_id] = ArtifactReference(
                    artifact_id=artifact_id,
                    kind="audio" if slot == "audio" else "artifact",
                    storage_locator=str(path),
                    role=slot,
                    producer_stage="local",
                )
                resolved_artifact_ids[slot] = artifact_id

            materialized = self._materializer.materialize(
                resolved_artifact_ids=resolved_artifact_ids,
                artifact_index=artifact_index,
                target_dir=temp_dir / "materialized",
            )

            engine_input = EngineInput(
                task_id=task_id,
                job_id=job_id,
                stage=stage,
                config=config,
                payload=payload,
                previous_outputs=previous_outputs,
                materialized_artifacts=materialized,
            )
            ctx = BatchTaskContext(
                runtime="local",
                instance="local-runner",
                task_id=task_id,
                job_id=job_id,
                stage=stage,
                metadata={"mode": "local"},
            )

            output = engine.process(engine_input, ctx)
            persisted = self._materializer.persist_produced(
                job_id=job_id,
                task_id=task_id,
                stage=stage,
                produced_artifacts=output.produced_artifacts,
            )

            return {
                "task_id": task_id,
                "job_id": job_id,
                "stage": stage,
                "data": output.to_dict(),
                "produced_artifacts": [
                    artifact.model_dump(mode="json", exclude_none=True)
                    for artifact in persisted
                ],
                "produced_artifact_ids": [
                    artifact.artifact_id for artifact in persisted
                ],
            }

    def _local_locator_builder(
        self,
        job_id: str,
        artifact_id: str,
        produced: ProducedArtifact,
    ) -> str:
        suffix = produced.local_path.suffix or ".bin"
        filename = f"{produced.logical_name}{suffix}"
        destination = (
            self.output_dir / "jobs" / job_id / "artifacts" / artifact_id / filename
        )
        return str(destination)


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
    if ":" not in engine_ref:
        raise ValueError(
            f"Engine reference must use '<module:Class>' format, got: {engine_ref}"
        )

    module_name, class_name = engine_ref.split(":", maxsplit=1)
    module = _import_engine_module(module_name)
    engine_type = getattr(module, class_name)

    if not isinstance(engine_type, type) or not issubclass(engine_type, Engine):
        raise TypeError(f"Engine class must inherit from Engine: {engine_ref}")

    return engine_type()


def _import_engine_module(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as import_error:
        module_path = _resolve_engine_module_path(module_name)
        if module_path is None:
            raise import_error

        loader_name = "dalston_local_runner_" + re.sub(
            r"[^a-zA-Z0-9_]", "_", str(module_path)
        )
        spec = importlib.util.spec_from_file_location(loader_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Unable to import engine module from path: {module_path}"
            ) from import_error

        module = importlib.util.module_from_spec(spec)
        sys.modules[loader_name] = module
        search_path = str(module_path.parent)
        path_added = False
        if search_path not in sys.path:
            sys.path.insert(0, search_path)
            path_added = True
        try:
            spec.loader.exec_module(module)
        finally:
            if path_added:
                sys.path.remove(search_path)
        return module


def _resolve_engine_module_path(module_name: str) -> Path | None:
    if "/" in module_name or module_name.endswith(".py"):
        candidate = Path(module_name)
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    candidate = Path(*module_name.split(".")).with_suffix(".py")
    if candidate.exists() and candidate.is_file():
        return candidate

    # Handle runtime IDs that include dots, for example:
    # engines.stt-diarize.pyannote-4.0.engine -> engines/stt-diarize/pyannote-4.0/engine.py
    parts = module_name.split(".")
    if len(parts) >= 4 and parts[0] == "engines" and parts[-1] == "engine":
        stage = parts[1]
        runtime = ".".join(parts[2:-1])
        runtime_candidate = Path("engines") / stage / runtime / "engine.py"
        if runtime_candidate.exists() and runtime_candidate.is_file():
            return runtime_candidate

    return None


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

    engine = _load_engine(args.engine)
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
