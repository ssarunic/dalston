"""Local filesystem runner for executing batch engines without Redis/S3."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from dalston.common.artifacts import ArtifactReference, ProducedArtifact
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
        engine,
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

            if stage == "merge":
                transcript_path = self.output_dir / "jobs" / job_id / "transcript.json"
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                transcript_path.write_text(
                    output.data.model_dump_json(indent=2)
                    if hasattr(output.data, "model_dump_json")
                    else str(output.data),
                    encoding="utf-8",
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
