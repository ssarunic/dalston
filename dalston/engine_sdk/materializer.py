"""Runner-side artifact materialization and persistence utilities."""

from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from pathlib import Path

from dalston.common.artifacts import (
    ArtifactReference,
    MaterializedArtifact,
    ProducedArtifact,
    build_task_artifact_id,
)
from dalston.engine_sdk import io


class ArtifactStore(ABC):
    """Transport adapter for artifact bytes."""

    @abstractmethod
    def download(self, locator: str, destination: Path) -> None:
        """Download remote artifact into destination path."""

    @abstractmethod
    def upload(self, source: Path, locator: str) -> None:
        """Upload local file to remote storage locator."""


class S3ArtifactStore(ArtifactStore):
    """Artifact transport using existing engine SDK I/O helpers."""

    def download(self, locator: str, destination: Path) -> None:
        io.download_file(locator, destination)

    def upload(self, source: Path, locator: str) -> None:
        io.upload_file(source, locator)


class LocalFilesystemArtifactStore(ArtifactStore):
    """Artifact transport that reads/writes directly from local filesystem."""

    def download(self, locator: str, destination: Path) -> None:
        source = Path(locator)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def upload(self, source: Path, locator: str) -> None:
        destination = Path(locator)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


class ArtifactMaterializer:
    """Materialize required artifacts and persist produced artifacts."""

    def __init__(
        self,
        store: ArtifactStore,
        locator_builder: Callable[[str, str, ProducedArtifact], str] | None = None,
    ) -> None:
        self.store = store
        self._locator_builder = locator_builder or self._default_locator_builder

    def materialize(
        self,
        *,
        resolved_artifact_ids: dict[str, str],
        artifact_index: dict[str, ArtifactReference],
        target_dir: Path,
    ) -> dict[str, MaterializedArtifact]:
        """Download task dependencies into local paths keyed by slot."""
        materialized: dict[str, MaterializedArtifact] = {}
        target_dir.mkdir(parents=True, exist_ok=True)

        for slot, artifact_id in resolved_artifact_ids.items():
            ref = artifact_index.get(artifact_id)
            if ref is None:
                raise ValueError(f"Missing artifact metadata for id {artifact_id}")

            suffix = Path(ref.storage_locator).suffix or ".bin"
            destination = target_dir / f"{slot}_{artifact_id}{suffix}"
            self.store.download(ref.storage_locator, destination)

            materialized[slot] = MaterializedArtifact(
                artifact_id=ref.artifact_id,
                kind=ref.kind,
                local_path=destination,
                channel=ref.channel,
                role=ref.role,
                media_type=ref.media_type,
            )

        return materialized

    def persist_produced(
        self,
        *,
        job_id: str,
        task_id: str,
        stage: str | None = None,
        produced_artifacts: Iterable[ProducedArtifact],
    ) -> list[ArtifactReference]:
        """Upload produced files and return persisted artifact references."""
        persisted: list[ArtifactReference] = []
        for produced in produced_artifacts:
            artifact_id = build_task_artifact_id(task_id, produced.logical_name)
            storage_locator = self._locator_builder(job_id, artifact_id, produced)
            self.store.upload(produced.local_path, storage_locator)

            persisted.append(
                ArtifactReference(
                    artifact_id=artifact_id,
                    kind=produced.kind,
                    storage_locator=storage_locator,
                    checksum=self._checksum(produced.local_path),
                    size=produced.local_path.stat().st_size,
                    media_type=produced.media_type,
                    channel=produced.channel,
                    role=produced.role,
                    producer_task_id=task_id,
                    producer_stage=stage,
                )
            )

        return persisted

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _bucket() -> str:
        from os import getenv

        return getenv("DALSTON_S3_BUCKET", "dalston-artifacts")

    @classmethod
    def _default_locator_builder(
        cls,
        job_id: str,
        artifact_id: str,
        produced: ProducedArtifact,
    ) -> str:
        suffix = produced.local_path.suffix or ".bin"
        filename = f"{produced.logical_name}{suffix}"
        return f"s3://{cls._bucket()}/jobs/{job_id}/artifacts/{artifact_id}/{filename}"
