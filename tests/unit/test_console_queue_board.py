"""Unit tests for the Queue Board service method and endpoint (M87).

Covers:

- `normalize_stage` channel-suffix stripping
- `PIPELINE_STAGES` canonical ordering
- `ConsoleService.get_queue_board()` flat-list shape, visibility rules,
  skipped-task exclusion, and last-hour summary stats
- `get_queue_board` endpoint stage-health aggregation (queue depth,
  heartbeat staleness, avg-duration mapping, channel normalization)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from dalston.gateway.api.console import (
    QueueBoardResponse,
    get_queue_board,
)
from dalston.gateway.services.console import (
    PIPELINE_STAGES,
    ConsoleService,
    QueueBoardDTO,
    QueueBoardJobDTO,
    normalize_stage,
)

# ---------------------------------------------------------------------------
# normalize_stage helper
# ---------------------------------------------------------------------------


class TestNormalizeStage:
    """`normalize_stage` mirrors DAGViewer.normalizeStage() on the frontend."""

    def test_passthrough_plain_stage(self):
        assert normalize_stage("transcribe") == "transcribe"

    def test_strips_single_digit_channel_suffix(self):
        assert normalize_stage("transcribe_ch0") == "transcribe"
        assert normalize_stage("transcribe_ch1") == "transcribe"

    def test_strips_multi_digit_channel_suffix(self):
        assert normalize_stage("transcribe_ch12") == "transcribe"

    def test_leaves_non_digit_suffix_alone(self):
        # "_ch" with a non-numeric suffix is not a channel marker.
        assert normalize_stage("transcribe_chX") == "transcribe_chX"

    def test_handles_underscore_in_stage_name(self):
        assert normalize_stage("pii_detect") == "pii_detect"
        assert normalize_stage("audio_redact_ch0") == "audio_redact"


class TestPipelineStagesOrdering:
    """The canonical stage order drives visible/hidden stage ordering."""

    def test_canonical_order(self):
        assert PIPELINE_STAGES == (
            "prepare",
            "transcribe",
            "align",
            "diarize",
            "pii_detect",
            "audio_redact",
            "merge",
        )


# ---------------------------------------------------------------------------
# ConsoleService.get_queue_board
# ---------------------------------------------------------------------------


def _orm_task(
    *,
    stage: str,
    status: str = "running",
    engine_id: str = "faster-whisper-base",
    task_id: UUID | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    ready_at: datetime | None = None,
    error: str | None = None,
) -> SimpleNamespace:
    """Build a stand-in for a TaskModel row (attribute access only)."""
    return SimpleNamespace(
        id=task_id or uuid4(),
        stage=stage,
        status=status,
        engine_id=engine_id,
        started_at=started_at,
        completed_at=completed_at,
        ready_at=ready_at,
        error=error,
    )


def _orm_job(
    *,
    status: str = "running",
    tasks: list[SimpleNamespace] | None = None,
    display_name: str | None = None,
    audio_duration: float | None = 12.5,
    job_id: UUID | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    """Build a stand-in for a JobModel row with eager-loaded tasks."""
    return SimpleNamespace(
        id=job_id or uuid4(),
        status=status,
        display_name=display_name,
        audio_duration=audio_duration,
        created_at=created_at or datetime.now(UTC),
        tasks=tasks or [],
    )


def _make_db_mock(
    active_jobs: list[SimpleNamespace],
    *,
    completed_count: int = 0,
    avg_seconds: float | None = None,
) -> MagicMock:
    """Build an AsyncSession stand-in that answers the two queries.

    The service issues exactly two `db.execute()` calls:
      1. active jobs (scalars().unique().all())
      2. last-hour summary (one() → .count / .avg_seconds)
    """
    jobs_result = MagicMock()
    jobs_result.scalars.return_value.unique.return_value.all.return_value = active_jobs

    recent_result = MagicMock()
    recent_result.one.return_value = SimpleNamespace(
        count=completed_count,
        avg_seconds=avg_seconds,
    )

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[jobs_result, recent_result])
    return db


@pytest.mark.asyncio
class TestGetQueueBoardService:
    """Tests for `ConsoleService.get_queue_board()`."""

    async def test_no_active_jobs_returns_empty_shape(self):
        db = _make_db_mock([])

        result = await ConsoleService().get_queue_board(db)

        assert isinstance(result, QueueBoardDTO)
        assert result.jobs == []
        assert result.tasks == []
        assert result.visible_stages == []
        # All canonical stages are hidden when no jobs are active.
        assert result.hidden_stages == list(PIPELINE_STAGES)
        assert result.completed_last_hour == 0
        assert result.avg_pipeline_ms is None

    async def test_visible_stages_in_canonical_order(self):
        # Tasks arrive out of order — the service must re-sort by PIPELINE_STAGES.
        job = _orm_job(
            tasks=[
                _orm_task(stage="merge", status="pending"),
                _orm_task(stage="prepare", status="running"),
                _orm_task(stage="transcribe", status="ready"),
            ]
        )
        db = _make_db_mock([job])

        result = await ConsoleService().get_queue_board(db)

        assert result.visible_stages == ["prepare", "transcribe", "merge"]
        # Hidden stages are the canonical stages not present, in order.
        assert result.hidden_stages == [
            "align",
            "diarize",
            "pii_detect",
            "audio_redact",
        ]

    async def test_channel_suffix_is_normalized(self):
        job = _orm_job(
            tasks=[
                _orm_task(stage="transcribe_ch0", status="running"),
                _orm_task(stage="transcribe_ch1", status="running"),
            ]
        )
        db = _make_db_mock([job])

        result = await ConsoleService().get_queue_board(db)

        # Both channel tasks normalize to the same base stage.
        assert [t.stage for t in result.tasks] == ["transcribe", "transcribe"]
        assert result.visible_stages == ["transcribe"]

    async def test_skipped_tasks_do_not_make_stage_visible(self):
        job = _orm_job(
            tasks=[
                _orm_task(stage="prepare", status="running"),
                _orm_task(stage="align", status="skipped"),
                _orm_task(stage="transcribe", status="running"),
            ]
        )
        db = _make_db_mock([job])

        result = await ConsoleService().get_queue_board(db)

        # Skipped align is still emitted as a task...
        stages_in_tasks = {t.stage for t in result.tasks}
        assert stages_in_tasks == {"prepare", "align", "transcribe"}
        # ...but it does not show up as a visible column.
        assert "align" not in result.visible_stages
        assert "align" in result.hidden_stages
        assert result.visible_stages == ["prepare", "transcribe"]

    async def test_flat_task_list_spans_multiple_jobs(self):
        job_a = _orm_job(
            tasks=[_orm_task(stage="transcribe", status="running")],
            display_name="clip-a",
        )
        job_b = _orm_job(
            tasks=[
                _orm_task(stage="transcribe", status="pending"),
                _orm_task(stage="merge", status="pending"),
            ],
            display_name="clip-b",
        )
        db = _make_db_mock([job_a, job_b])

        result = await ConsoleService().get_queue_board(db)

        assert len(result.jobs) == 2
        assert {j.display_name for j in result.jobs} == {"clip-a", "clip-b"}
        # Flat list: one task from job_a + two from job_b = 3 total.
        assert len(result.tasks) == 3
        # Each task carries the originating job_id.
        task_job_ids = {t.job_id for t in result.tasks}
        assert task_job_ids == {job_a.id, job_b.id}

    async def test_duration_and_wait_are_computed(self):
        ready = datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC)
        started = ready + timedelta(seconds=2)
        completed = started + timedelta(seconds=5)
        job = _orm_job(
            tasks=[
                _orm_task(
                    stage="transcribe",
                    status="completed",
                    ready_at=ready,
                    started_at=started,
                    completed_at=completed,
                )
            ]
        )
        db = _make_db_mock([job])

        result = await ConsoleService().get_queue_board(db)

        task = result.tasks[0]
        assert task.duration_ms == 5000
        assert task.wait_ms == 2000

    async def test_empty_display_name_coerced_to_none(self):
        # The service uses `job.display_name or None` so empty strings
        # surface as None to the API layer.
        job = _orm_job(display_name="", tasks=[_orm_task(stage="transcribe")])
        db = _make_db_mock([job])

        result = await ConsoleService().get_queue_board(db)

        assert result.jobs[0].display_name is None

    async def test_last_hour_summary_populated(self):
        db = _make_db_mock(
            [_orm_job(tasks=[_orm_task(stage="transcribe")])],
            completed_count=4,
            avg_seconds=12.3456,
        )

        result = await ConsoleService().get_queue_board(db)

        assert result.completed_last_hour == 4
        # Rounded to 1 decimal place, in milliseconds.
        assert result.avg_pipeline_ms == 12345.6

    async def test_last_hour_summary_handles_null_avg(self):
        db = _make_db_mock(
            [_orm_job(tasks=[_orm_task(stage="transcribe")])],
            completed_count=0,
            avg_seconds=None,
        )

        result = await ConsoleService().get_queue_board(db)

        assert result.completed_last_hour == 0
        assert result.avg_pipeline_ms is None


# ---------------------------------------------------------------------------
# get_queue_board endpoint
# ---------------------------------------------------------------------------


def _mock_security_manager() -> MagicMock:
    sm = MagicMock()
    sm.require_permission = MagicMock()
    return sm


def _catalog_entry(engine_id: str, stage: str) -> SimpleNamespace:
    """Build a stand-in for a CatalogEntry (attribute access only)."""
    return SimpleNamespace(
        engine_id=engine_id,
        capabilities=SimpleNamespace(stages=[stage]),
    )


def _fresh_heartbeat(
    engine_id: str,
    *,
    instance_id: str,
    current_task: str = "",
) -> dict[str, str]:
    return {
        "instance_id": instance_id,
        "engine_id": engine_id,
        "last_heartbeat": datetime.now(UTC).isoformat(),
        "current_task": current_task,
    }


@pytest.mark.asyncio
class TestGetQueueBoardEndpoint:
    """Tests for the `get_queue_board` FastAPI handler."""

    async def _call(
        self,
        *,
        board: QueueBoardDTO,
        redis: AsyncMock,
        db: MagicMock,
        catalog_entries: list[SimpleNamespace],
        stage_avg_rows: list[SimpleNamespace] | None = None,
    ) -> QueueBoardResponse:
        """Invoke the endpoint handler with mocked dependencies."""
        console_service = MagicMock()
        console_service.get_queue_board = AsyncMock(return_value=board)

        catalog = MagicMock()
        catalog.get_all_engines.return_value = catalog_entries

        # The endpoint issues one extra db.execute() for per-stage averages.
        stage_result = MagicMock()
        stage_result.all.return_value = stage_avg_rows or []
        db.execute = AsyncMock(return_value=stage_result)

        with (
            patch(
                "dalston.gateway.api.console.get_security_manager",
                return_value=_mock_security_manager(),
            ),
            patch(
                "dalston.orchestrator.catalog.get_catalog",
                return_value=catalog,
            ),
        ):
            return await get_queue_board(
                principal=MagicMock(),
                db=db,
                redis=redis,
                console_service=console_service,
            )

    async def test_empty_board_returns_empty_response(self):
        board = QueueBoardDTO(
            jobs=[],
            tasks=[],
            visible_stages=[],
            hidden_stages=list(PIPELINE_STAGES),
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.hgetall = AsyncMock(return_value={})

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=[],
        )

        assert result.jobs == []
        assert result.tasks == []
        assert result.stages == []
        assert result.hidden_stages == list(PIPELINE_STAGES)

    async def test_queue_depth_aggregated_per_visible_stage(self):
        board = QueueBoardDTO(
            jobs=[
                QueueBoardJobDTO(
                    job_id=uuid4(),
                    display_name="clip",
                    status="running",
                    created_at=datetime.now(UTC),
                    audio_duration_seconds=10.0,
                )
            ],
            tasks=[],
            visible_stages=["transcribe"],
            hidden_stages=[s for s in PIPELINE_STAGES if s != "transcribe"],
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )

        # Two transcribe engines + one diarize engine. Only transcribe
        # stages are visible so only transcribe engines should be scanned.
        catalog_entries = [
            _catalog_entry("faster-whisper-base", "transcribe"),
            _catalog_entry("faster-whisper-large", "transcribe"),
            _catalog_entry("pyannote-3.1", "diarize"),
        ]

        # `_get_stream_backlog` reads `lag` from the "engines" consumer
        # group via XINFO GROUPS, so we fake that shape per stream.
        backlog = {
            "dalston:stream:faster-whisper-base": 3,
            "dalston:stream:faster-whisper-large": 5,
            "dalston:stream:pyannote-3.1": 99,  # should NOT be counted
        }

        async def xinfo_groups(key: str) -> list[dict]:
            return [{"name": "engines", "lag": backlog.get(key, 0)}]

        redis = AsyncMock()
        redis.xinfo_groups = AsyncMock(side_effect=xinfo_groups)
        redis.smembers = AsyncMock(return_value=set())
        redis.hgetall = AsyncMock(return_value={})

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=catalog_entries,
        )

        assert len(result.stages) == 1
        health = result.stages[0]
        assert health.stage == "transcribe"
        assert health.queue_depth == 8  # 3 + 5, diarize excluded
        assert health.total_workers == 0
        assert health.processing == 0

    async def test_heartbeats_count_fresh_workers_only(self):
        board = QueueBoardDTO(
            jobs=[
                QueueBoardJobDTO(
                    job_id=uuid4(),
                    display_name=None,
                    status="running",
                    created_at=datetime.now(UTC),
                    audio_duration_seconds=None,
                )
            ],
            tasks=[],
            visible_stages=["transcribe"],
            hidden_stages=[s for s in PIPELINE_STAGES if s != "transcribe"],
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )
        catalog_entries = [_catalog_entry("faster-whisper-base", "transcribe")]

        # Three heartbeats: one fresh + idle, one fresh + busy, one stale.
        stale_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        hb_fresh_idle = _fresh_heartbeat("faster-whisper-base", instance_id="fw-1")
        hb_fresh_busy = _fresh_heartbeat(
            "faster-whisper-base", instance_id="fw-2", current_task="task-xyz"
        )
        hb_stale = {
            "instance_id": "fw-3",
            "engine_id": "faster-whisper-base",
            "last_heartbeat": stale_time,
            "current_task": "task-abc",
        }

        async def hgetall(key: str) -> dict[str, str]:
            if key.endswith("fw-1"):
                return hb_fresh_idle
            if key.endswith("fw-2"):
                return hb_fresh_busy
            if key.endswith("fw-3"):
                return hb_stale
            return {}

        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"fw-1", "fw-2", "fw-3"})
        redis.hgetall = AsyncMock(side_effect=hgetall)
        redis.xinfo_groups = AsyncMock(return_value=[])

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=catalog_entries,
        )

        health = result.stages[0]
        # Two fresh workers counted; stale one dropped.
        assert health.total_workers == 2
        # Only the busy fresh worker counts as "processing".
        assert health.processing == 1

    async def test_stage_avg_rows_map_onto_visible_stage(self):
        board = QueueBoardDTO(
            jobs=[
                QueueBoardJobDTO(
                    job_id=uuid4(),
                    display_name=None,
                    status="running",
                    created_at=datetime.now(UTC),
                    audio_duration_seconds=None,
                )
            ],
            tasks=[],
            visible_stages=["transcribe"],
            hidden_stages=[s for s in PIPELINE_STAGES if s != "transcribe"],
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )
        catalog_entries: list[SimpleNamespace] = []

        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.hgetall = AsyncMock(return_value={})
        redis.xinfo_groups = AsyncMock(return_value=[])

        # Stage rows mix channel-suffixed and plain stages. Only the
        # normalized transcribe row should populate the visible stage,
        # and the unrelated diarize row should be silently ignored.
        stage_avg_rows = [
            SimpleNamespace(stage="transcribe_ch0", row_count=3, avg_seconds=4.2),
            SimpleNamespace(stage="diarize", row_count=10, avg_seconds=1.0),
        ]

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=catalog_entries,
            stage_avg_rows=stage_avg_rows,
        )

        health = result.stages[0]
        # transcribe_ch0 → transcribe, 4.2s → 4200 ms.
        assert health.avg_duration_ms == 4200.0

    async def test_channel_rows_are_weighted_averaged(self):
        """Multiple rows folding into the same normalized stage must
        produce a count-weighted average, not overwrite each other."""
        board = QueueBoardDTO(
            jobs=[
                QueueBoardJobDTO(
                    job_id=uuid4(),
                    display_name=None,
                    status="running",
                    created_at=datetime.now(UTC),
                    audio_duration_seconds=None,
                )
            ],
            tasks=[],
            visible_stages=["transcribe"],
            hidden_stages=[s for s in PIPELINE_STAGES if s != "transcribe"],
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )
        catalog_entries: list[SimpleNamespace] = []

        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.hgetall = AsyncMock(return_value={})
        redis.xinfo_groups = AsyncMock(return_value=[])

        # transcribe (2 rows, avg 2s), transcribe_ch0 (8 rows, avg 4s),
        # transcribe_ch1 (0 rows should be ignored).
        # Weighted mean = (2*2 + 8*4) / (2 + 8) = 36 / 10 = 3.6s → 3600 ms.
        stage_avg_rows = [
            SimpleNamespace(stage="transcribe", row_count=2, avg_seconds=2.0),
            SimpleNamespace(stage="transcribe_ch0", row_count=8, avg_seconds=4.0),
            SimpleNamespace(stage="transcribe_ch1", row_count=0, avg_seconds=99.0),
        ]

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=catalog_entries,
            stage_avg_rows=stage_avg_rows,
        )

        assert result.stages[0].avg_duration_ms == 3600.0

    async def test_no_visible_stages_skips_catalog_walk(self):
        board = QueueBoardDTO(
            jobs=[
                QueueBoardJobDTO(
                    job_id=uuid4(),
                    display_name=None,
                    status="running",
                    created_at=datetime.now(UTC),
                    audio_duration_seconds=None,
                )
            ],
            tasks=[],
            visible_stages=[],
            hidden_stages=list(PIPELINE_STAGES),
            completed_last_hour=0,
            avg_pipeline_ms=None,
        )

        # Even though the catalog has entries, none should be aggregated.
        catalog_entries = [_catalog_entry("faster-whisper-base", "transcribe")]

        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())
        redis.hgetall = AsyncMock(return_value={})
        redis.xinfo_groups = AsyncMock(return_value=[{"name": "engines", "lag": 42}])

        result = await self._call(
            board=board,
            redis=redis,
            db=MagicMock(),
            catalog_entries=catalog_entries,
        )

        assert result.stages == []
        # XINFO GROUPS must not be called since no stage is visible.
        redis.xinfo_groups.assert_not_called()
