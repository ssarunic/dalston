"""Phase-1 tests for runner-side artifact materialization."""

from __future__ import annotations

from pathlib import Path

from dalston.common.artifacts import ArtifactReference, ProducedArtifact
from dalston.engine_sdk.materializer import ArtifactMaterializer, ArtifactStore


class _MemoryStore(ArtifactStore):
    def __init__(self) -> None:
        self.remote_files: dict[str, bytes] = {}

    def download(self, locator: str, destination: Path) -> None:
        payload = self.remote_files[locator]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    def upload(self, source: Path, locator: str) -> None:
        self.remote_files[locator] = source.read_bytes()


def test_materialize_downloads_resolved_artifacts(tmp_path: Path) -> None:
    store = _MemoryStore()
    store.remote_files["s3://bucket/jobs/j1/a1.wav"] = b"audio-bytes"

    materializer = ArtifactMaterializer(store=store)
    artifact_index = {
        "a1": ArtifactReference(
            artifact_id="a1",
            kind="audio",
            storage_locator="s3://bucket/jobs/j1/a1.wav",
            media_type="audio/wav",
        )
    }

    materialized = materializer.materialize(
        resolved_artifact_ids={"audio": "a1"},
        artifact_index=artifact_index,
        target_dir=tmp_path / "mat",
    )

    assert set(materialized.keys()) == {"audio"}
    assert materialized["audio"].artifact_id == "a1"
    assert materialized["audio"].local_path.read_bytes() == b"audio-bytes"


def test_persist_uploads_engine_outputs_and_returns_references(tmp_path: Path) -> None:
    store = _MemoryStore()
    local_file = tmp_path / "prepared.wav"
    local_file.write_bytes(b"prepared-audio")

    materializer = ArtifactMaterializer(store=store)
    produced = [
        ProducedArtifact(
            logical_name="prepared_audio",
            local_path=local_file,
            kind="audio",
            role="prepared",
            media_type="audio/wav",
        )
    ]

    refs = materializer.persist_produced(
        job_id="job-123",
        task_id="task-456",
        produced_artifacts=produced,
    )

    assert len(refs) == 1
    assert refs[0].artifact_id.startswith("task-456:prepared_audio")
    assert refs[0].kind == "audio"
    assert refs[0].role == "prepared"
    assert refs[0].media_type == "audio/wav"
    assert refs[0].storage_locator in store.remote_files
