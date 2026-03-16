"""M52 tests for engine runner stream polling contract."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import dalston.engine_sdk.runner as runner_module
from dalston.common.artifacts import ProducedArtifact
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class _NoopEngine(Engine):
    def process(
        self,
        input: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        del input
        del ctx
        return TaskResponse(data={})


def test_runner_has_no_legacy_stage_fallback_method() -> None:
    assert not hasattr(EngineRunner, "_candidate_stream_ids")


def test_runner_polls_engine_id_stream_only(monkeypatch) -> None:
    with patch.dict(os.environ, {"DALSTON_ENGINE_ID": "engine_id-only"}):
        runner = EngineRunner(_NoopEngine())
    runner._redis = MagicMock()
    runner._stage = "transcribe"

    seen_stages: list[str] = []

    def fake_claim_stale(*, stage: str, **kwargs):
        del kwargs
        seen_stages.append(stage)
        return []

    def fake_read_task(*, stage: str, **kwargs):
        del kwargs
        seen_stages.append(stage)
        return None

    monkeypatch.setattr(
        runner_module,
        "claim_stale_from_dead_engines",
        lambda redis_client, stage, consumer, min_idle_ms, count: fake_claim_stale(
            stage=stage,
            redis_client=redis_client,
            consumer=consumer,
            min_idle_ms=min_idle_ms,
            count=count,
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "read_task",
        lambda redis_client, stage, consumer, block_ms: fake_read_task(
            stage=stage,
            redis_client=redis_client,
            consumer=consumer,
            block_ms=block_ms,
        ),
    )

    runner._poll_and_process()

    assert seen_stages == ["engine_id-only", "engine_id-only"]


def test_runner_uses_final_transcript_artifact_for_canonical_uri(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DALSTON_S3_BUCKET", "test-bucket")
    with patch.dict(os.environ, {"DALSTON_ENGINE_ID": "engine_id-only"}):
        runner = EngineRunner(_NoopEngine())

    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text('{"job_id": "job-1"}', encoding="utf-8")

    upload_json_calls: list[tuple[dict, str]] = []
    upload_file_calls: list[tuple[Path, str]] = []

    monkeypatch.setattr(
        runner_module.io,
        "upload_json",
        lambda payload, locator: upload_json_calls.append((payload, locator)),
    )
    monkeypatch.setattr(
        runner_module.io,
        "upload_file",
        lambda source, locator: upload_file_calls.append((source, locator)),
    )
    monkeypatch.setattr(
        runner,
        "_get_task_metadata",
        lambda _task_id: {"job_id": "job-1", "stage": "merge"},
    )
    runner._redis = MagicMock()
    runner.s3_bucket = "test-bucket"

    output = TaskResponse(
        data={"job_id": "job-1"},
        produced_artifacts=[
            ProducedArtifact(
                logical_name="transcript",
                local_path=transcript_path,
                kind="transcript",
                role="final",
                media_type="application/json",
            )
        ],
    )

    runner._save_task_output(
        task_id="task-1",
        job_id="job-1",
        output=output,
        processing_time=1.23,
    )

    assert upload_file_calls == [
        (transcript_path, "s3://test-bucket/jobs/job-1/transcript.json")
    ]
    assert len(upload_json_calls) == 1
    output_payload, output_locator = upload_json_calls[0]
    assert output_locator == "s3://test-bucket/jobs/job-1/tasks/task-1/response.json"
    assert (
        output_payload["canonical_transcript_uri"]
        == "s3://test-bucket/jobs/job-1/transcript.json"
    )
