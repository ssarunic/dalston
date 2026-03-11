"""Regression tests for concurrent job counter handling in job.created failures."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import JobStatus
from dalston.orchestrator.engine_selector import (
    NoCapableEngineError,
    NoDownloadedModelError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("build_error", "expected_error_snippet"),
    [
        (NoDownloadedModelError(engine_id="nemo"), "No downloaded models"),
        (
            NoCapableEngineError(
                stage="transcribe",
                requirements={},
                candidates=[],
                catalog_alternatives=[],
            ),
            "No running engine can handle this job",
        ),
    ],
)
async def test_handle_job_created_decrements_counter_on_dag_build_failure(
    build_error: Exception, expected_error_snippet: str
) -> None:
    """DAG build failures should still decrement concurrent counters exactly once."""
    from dalston.orchestrator.handlers import handle_job_created

    job_id = uuid4()
    tenant_id = uuid4()

    job = MagicMock()
    job.id = job_id
    job.tenant_id = tenant_id
    job.status = JobStatus.PENDING.value
    job.audio_uri = "s3://dalston/test.wav"
    job.parameters = {}
    job.error = None
    job.completed_at = None

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    # Existing-task check should see no tasks.
    no_existing_tasks = MagicMock()
    no_existing_tasks.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_existing_tasks)
    db.commit = AsyncMock()

    redis = AsyncMock()
    settings = MagicMock()
    registry = MagicMock()

    with (
        patch("dalston.orchestrator.handlers.get_catalog", return_value=MagicMock()),
        patch(
            "dalston.orchestrator.handlers.build_task_dag",
            new_callable=AsyncMock,
            side_effect=build_error,
        ),
        patch(
            "dalston.orchestrator.handlers._decrement_concurrent_jobs",
            new_callable=AsyncMock,
        ) as mock_decrement,
        patch(
            "dalston.orchestrator.handlers.publish_job_failed",
            new_callable=AsyncMock,
        ) as mock_publish_failed,
    ):
        await handle_job_created(
            job_id=job_id,
            db=db,
            redis=redis,
            settings=settings,
            registry=registry,
        )

    assert job.status == JobStatus.FAILED.value
    assert job.error is not None
    assert expected_error_snippet in job.error
    assert isinstance(job.completed_at, datetime)
    assert job.completed_at.tzinfo == UTC

    db.commit.assert_awaited_once()
    mock_decrement.assert_awaited_once_with(redis, job_id, tenant_id)
    mock_publish_failed.assert_awaited_once_with(redis, job_id, job.error)
