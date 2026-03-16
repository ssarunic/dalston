from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import Task, TaskStatus
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.exceptions import CatalogValidationError
from dalston.orchestrator.scheduler import queue_task


class MockSettings:
    s3_bucket = "test-bucket"
    s3_endpoint = "http://localhost:9000"
    s3_access_key = "test-key"
    s3_secret_key = "test-secret"
    engine_unavailable_behavior = "fail_fast"
    engine_wait_timeout_seconds = 300


class _NoopEngine(Engine):
    def process(
        self,
        input: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        del input
        del ctx
        return TaskResponse(data={})

    def get_capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_id="container-engine_id",
            version="test",
            stages=["transcribe"],
        )


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.sadd = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.is_engine_available = AsyncMock(return_value=True)
    registry.get_engine = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def sample_task():
    return Task(
        id=uuid4(),
        job_id=uuid4(),
        stage="transcribe",
        engine_id="container-engine_id",
        status=TaskStatus.READY,
        request_uri="s3://bucket/audio.wav",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config={"language": "en"},
    )


def _catalog_entry(engine_id: str, execution_profile: str) -> CatalogEntry:
    return CatalogEntry(
        engine_id=engine_id,
        image=f"dalston/{engine_id}:latest",
        capabilities=EngineCapabilities(
            engine_id=engine_id,
            version="test",
            stages=["transcribe"],
        ),
        execution_profile=execution_profile,
    )


@pytest.mark.asyncio
async def test_queue_task_records_container_execution_profile(
    mock_redis, mock_registry, sample_task
) -> None:
    catalog = EngineCatalog(
        {"container-engine_id": _catalog_entry("container-engine_id", "container")}
    )

    with (
        patch(
            "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
        ) as add_task,
        patch(
            "dalston.orchestrator.scheduler.write_task_request",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        add_task.return_value = "1-0"

        await queue_task(
            redis=mock_redis,
            task=sample_task,
            settings=MockSettings(),
            registry=mock_registry,
            catalog=catalog,
        )

    metadata_mapping = mock_redis.hset.await_args_list[0].kwargs["mapping"]
    assert metadata_mapping["execution_profile"] == "container"


@pytest.mark.asyncio
async def test_queue_task_rejects_non_container_engine_id_on_distributed_path(
    mock_redis, mock_registry, sample_task
) -> None:
    catalog = EngineCatalog(
        {"container-engine_id": _catalog_entry("container-engine_id", "venv")}
    )

    with (
        patch(
            "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
        ) as add_task,
        patch(
            "dalston.orchestrator.scheduler.write_task_request",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(
            CatalogValidationError,
            match="cannot be queued on the distributed container path",
        ):
            await queue_task(
                redis=mock_redis,
                task=sample_task,
                settings=MockSettings(),
                registry=mock_registry,
                catalog=catalog,
            )

    add_task.assert_not_called()


def test_engine_runner_rejects_non_container_engine_id() -> None:
    with patch.dict("os.environ", {"DALSTON_ENGINE_ID": "container-engine_id"}):
        runner = EngineRunner(_NoopEngine())

    registry = MagicMock()
    catalog = MagicMock()
    catalog.get_engine.return_value = _catalog_entry("container-engine_id", "venv")

    with (
        patch.object(runner, "_setup_signal_handlers"),
        patch.object(runner, "_start_metrics_server"),
        patch("dalston.engine_sdk.runner.UnifiedRegistryWriter", return_value=registry),
        patch("dalston.engine_sdk.runner.get_catalog", return_value=catalog),
    ):
        with pytest.raises(
            RuntimeError,
            match="cannot start as a distributed container worker",
        ):
            runner.run()
