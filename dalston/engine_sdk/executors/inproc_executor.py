"""In-process executor backed by the local runner artifact contract."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from dalston.common.artifacts import ArtifactReference, ProducedArtifact
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.executors.base import ExecutionRequest, RuntimeExecutor
from dalston.engine_sdk.materializer import (
    ArtifactMaterializer,
    LocalFilesystemArtifactStore,
)
from dalston.engine_sdk.types import TaskRequest


class InProcExecutor(RuntimeExecutor):
    """Execute a task in the current Python process."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._store = LocalFilesystemArtifactStore()
        self._materializer = ArtifactMaterializer(
            store=self._store,
            locator_builder=self._local_locator_builder,
        )

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        if request.engine is None:
            raise ValueError("InProcExecutor requires ExecutionRequest.engine")

        with tempfile.TemporaryDirectory(prefix="dalston-local-runner-") as tmp:
            temp_dir = Path(tmp)
            artifact_index: dict[str, ArtifactReference] = {}
            resolved_artifact_ids: dict[str, str] = {}
            for slot, path in request.artifacts.items():
                artifact_id = f"{request.job_id}:local:{slot}"
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

            task_request = TaskRequest(
                task_id=request.task_id,
                job_id=request.job_id,
                stage=request.stage,
                config=request.config,
                payload=request.payload,
                previous_responses=request.previous_responses,
                materialized_artifacts=materialized,
            )
            ctx = BatchTaskContext(
                engine_id=request.engine_id,
                instance=request.instance,
                task_id=request.task_id,
                job_id=request.job_id,
                stage=request.stage,
                metadata=request.metadata,
            )

            response = request.engine.process(task_request, ctx)
            persisted = self._materializer.persist_produced(
                job_id=request.job_id,
                task_id=request.task_id,
                stage=request.stage,
                produced_artifacts=response.produced_artifacts,
            )

            return {
                "task_id": request.task_id,
                "job_id": request.job_id,
                "stage": request.stage,
                "data": response.to_dict(),
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
        task_id: str,
        produced: ProducedArtifact,
    ) -> str:
        suffix = produced.local_path.suffix or ".bin"
        filename = f"{produced.logical_name}{suffix}"
        destination = self.output_dir / "jobs" / job_id / "tasks" / task_id / filename
        return str(destination)
