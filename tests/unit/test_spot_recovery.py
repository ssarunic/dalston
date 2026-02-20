"""Unit tests for spot instance recovery scenarios.

Tests that the system correctly handles engine failures and spot instance
replacements, ensuring tasks are properly recovered when engines die.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import TaskStatus
from dalston.common.streams_types import PendingTask


class TestEngineRunnerInstanceId:
    """Tests for engine runner instance-unique consumer ID generation."""

    def test_generates_unique_instance_id(self):
        """Test that each EngineRunner generates a unique instance_id."""
        with patch.dict("os.environ", {"ENGINE_ID": "whisper-cpu"}):
            from dalston.engine_sdk.runner import EngineRunner

            mock_engine = MagicMock()
            mock_engine.get_capabilities.return_value = MagicMock(stages=["transcribe"])

            # Create two runner instances
            runner1 = EngineRunner(mock_engine)
            runner2 = EngineRunner(mock_engine)

            # Both should have the same logical engine_id
            assert runner1.engine_id == "whisper-cpu"
            assert runner2.engine_id == "whisper-cpu"

            # But different instance_ids
            assert runner1.instance_id != runner2.instance_id
            assert runner1.instance_id.startswith("whisper-cpu-")
            assert runner2.instance_id.startswith("whisper-cpu-")

    def test_instance_id_is_12_char_hex_suffix(self):
        """Test that instance_id has expected format."""
        with patch.dict("os.environ", {"ENGINE_ID": "faster-whisper"}):
            from dalston.engine_sdk.runner import EngineRunner

            mock_engine = MagicMock()
            mock_engine.get_capabilities.return_value = MagicMock(stages=["transcribe"])

            runner = EngineRunner(mock_engine)

            # Format: {engine_id}-{12_char_hex}
            # engine_id may contain hyphens, so extract suffix by removing prefix
            assert runner.instance_id.startswith("faster-whisper-")
            suffix = runner.instance_id.replace("faster-whisper-", "")
            assert len(suffix) == 12
            int(suffix, 16)  # Should not raise - valid hex


class TestReconcilerTerminalStatusAck:
    """Tests for reconciler only ACKing terminal status tasks in PEL."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_does_not_ack_ready_task_in_pel(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that READY tasks in PEL are NOT ACKed (race window protection)."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        # Create mock DB session that returns READY status
        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.READY.value)]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.get_pending", new_callable=AsyncMock
            ),
        ):
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(
                mock_db_session, pel_by_stage
            )

            # Should NOT ack READY tasks
            assert count == 0
            mock_ack.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_ack_running_task_in_pel(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that RUNNING tasks in PEL are NOT ACKed (normal state)."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.RUNNING.value)]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        with patch(
            "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
        ) as mock_ack:
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(
                mock_db_session, pel_by_stage
            )

            assert count == 0
            mock_ack.assert_not_called()

    @pytest.mark.asyncio
    async def test_acks_completed_task_in_pel(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that COMPLETED tasks in PEL ARE ACKed."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.COMPLETED.value)]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        # Mock get_pending to return entry with matching task_id
        mock_pending_entry = MagicMock()
        mock_pending_entry.task_id = str(task_id)
        mock_pending_entry.message_id = "123-0"

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.get_pending",
                new_callable=AsyncMock,
                return_value=[mock_pending_entry],
            ),
        ):
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(
                mock_db_session, pel_by_stage
            )

            # Should ACK completed task
            assert count == 1
            mock_ack.assert_called_once_with(mock_redis, "transcribe", "123-0")

    @pytest.mark.asyncio
    async def test_acks_failed_task_in_pel(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that FAILED tasks in PEL ARE ACKed."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.FAILED.value)]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        mock_pending_entry = MagicMock()
        mock_pending_entry.task_id = str(task_id)
        mock_pending_entry.message_id = "456-0"

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.get_pending",
                new_callable=AsyncMock,
                return_value=[mock_pending_entry],
            ),
        ):
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(
                mock_db_session, pel_by_stage
            )

            assert count == 1
            mock_ack.assert_called_once()


class TestReconcilerStaleReadyRecovery:
    """Tests for recovering stuck READY tasks from dead engines."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_recovers_stale_ready_task_from_dead_engine(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test recovery of READY task when engine is dead."""
        from dalston.orchestrator.reconciler import (
            ORPHAN_THRESHOLD_SECONDS,
            ReconciliationSweeper,
        )

        task_id = uuid4()
        job_id = uuid4()

        # Mock task in READY state
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.READY.value

        mock_db_session.get = AsyncMock(return_value=mock_task)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        # PEL entry is stale (> threshold)
        pel_entry = PendingTask(
            message_id="123-0",
            task_id=str(task_id),
            consumer="whisper-cpu-dead123abc",
            idle_ms=(ORPHAN_THRESHOLD_SECONDS + 60) * 1000,  # > threshold
            delivery_count=1,
        )

        # Track call order to verify add_task is called BEFORE ack_task
        # This is critical for crash safety - if we ACK first and crash,
        # the task is lost. If we add first and crash, we have a duplicate
        # which is recoverable.
        call_order = []

        async def track_add(*args, **kwargs):
            call_order.append("add_task")

        async def track_ack(*args, **kwargs):
            call_order.append("ack_task")

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", side_effect=track_ack
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.add_task", side_effect=track_add
            ) as mock_add,
            patch(
                "dalston.orchestrator.reconciler.is_engine_alive",
                new_callable=AsyncMock,
                return_value=False,  # Engine is dead
            ),
        ):
            pel_entries_by_stage = {"transcribe": {str(task_id): pel_entry}}

            count = await sweeper._reconcile_stale_ready_tasks(
                mock_db_session, pel_entries_by_stage
            )

            # Should add to stream FIRST, then ACK old entry
            assert count == 1
            mock_add.assert_called_once()
            mock_ack.assert_called_once_with(mock_redis, "transcribe", "123-0")

            # CRITICAL: Verify add_task was called BEFORE ack_task
            # This ensures crash safety - duplicates are recoverable, lost tasks are not
            assert call_order == ["add_task", "ack_task"], (
                f"add_task must be called before ack_task for crash safety, "
                f"got order: {call_order}"
            )

            # Verify add_task was called with correct parameters
            add_call_kwargs = mock_add.call_args[1]
            assert add_call_kwargs["stage"] == "transcribe"
            assert add_call_kwargs["task_id"] == str(task_id)
            assert add_call_kwargs["job_id"] == str(job_id)

    @pytest.mark.asyncio
    async def test_skips_stale_ready_task_when_engine_alive(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that stale READY tasks from live engines are not recovered."""
        from dalston.orchestrator.reconciler import (
            ORPHAN_THRESHOLD_SECONDS,
            ReconciliationSweeper,
        )

        task_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = uuid4()
        mock_task.status = TaskStatus.READY.value

        mock_db_session.get = AsyncMock(return_value=mock_task)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        pel_entry = PendingTask(
            message_id="123-0",
            task_id=str(task_id),
            consumer="whisper-cpu-alive456def",
            idle_ms=(ORPHAN_THRESHOLD_SECONDS + 60) * 1000,
            delivery_count=1,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.add_task", new_callable=AsyncMock
            ) as mock_add,
            patch(
                "dalston.orchestrator.reconciler.is_engine_alive",
                new_callable=AsyncMock,
                return_value=True,  # Engine is alive
            ),
        ):
            pel_entries_by_stage = {"transcribe": {str(task_id): pel_entry}}

            count = await sweeper._reconcile_stale_ready_tasks(
                mock_db_session, pel_entries_by_stage
            )

            # Should NOT recover when engine is alive
            assert count == 0
            mock_ack.assert_not_called()
            mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_stale_ready_task(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that non-stale READY tasks are not touched."""
        from dalston.orchestrator.reconciler import (
            ORPHAN_THRESHOLD_SECONDS,
            ReconciliationSweeper,
        )

        task_id = uuid4()

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        # PEL entry is NOT stale (below threshold)
        pel_entry = PendingTask(
            message_id="123-0",
            task_id=str(task_id),
            consumer="whisper-cpu-instance123",
            idle_ms=(ORPHAN_THRESHOLD_SECONDS - 60) * 1000,  # < threshold
            delivery_count=1,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.add_task", new_callable=AsyncMock
            ) as mock_add,
        ):
            pel_entries_by_stage = {"transcribe": {str(task_id): pel_entry}}

            count = await sweeper._reconcile_stale_ready_tasks(
                mock_db_session, pel_entries_by_stage
            )

            # Should not touch non-stale entries
            assert count == 0
            mock_ack.assert_not_called()
            mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_running_task_even_if_stale(
        self, mock_redis, mock_db_session, mock_settings
    ):
        """Test that RUNNING tasks are not recovered (handled by scanner)."""
        from dalston.orchestrator.reconciler import (
            ORPHAN_THRESHOLD_SECONDS,
            ReconciliationSweeper,
        )

        task_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = uuid4()
        mock_task.status = TaskStatus.RUNNING.value  # RUNNING, not READY

        mock_db_session.get = AsyncMock(return_value=mock_task)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        pel_entry = PendingTask(
            message_id="123-0",
            task_id=str(task_id),
            consumer="whisper-cpu-dead123abc",
            idle_ms=(ORPHAN_THRESHOLD_SECONDS + 60) * 1000,
            delivery_count=1,
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.ack_task", new_callable=AsyncMock
            ) as mock_ack,
            patch(
                "dalston.orchestrator.reconciler.add_task", new_callable=AsyncMock
            ) as mock_add,
            patch(
                "dalston.orchestrator.reconciler.is_engine_alive",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            pel_entries_by_stage = {"transcribe": {str(task_id): pel_entry}}

            count = await sweeper._reconcile_stale_ready_tasks(
                mock_db_session, pel_entries_by_stage
            )

            # Should not recover RUNNING tasks (scanner handles those)
            assert count == 0
            mock_ack.assert_not_called()
            mock_add.assert_not_called()


class TestIsEngineAlive:
    """Tests for engine liveness checks with instance-unique consumer IDs."""

    def test_dead_engine_returns_false(self):
        """Test that missing heartbeat key returns False."""
        from dalston.common.streams_sync import is_engine_alive

        mock_redis = MagicMock()

        # No heartbeat data (key doesn't exist)
        mock_redis.hgetall.return_value = {}

        result = is_engine_alive(mock_redis, "whisper-cpu-deadinst01")

        assert result is False

    def test_alive_engine_returns_true(self):
        """Test that fresh heartbeat returns True."""
        from dalston.common.streams_sync import is_engine_alive

        mock_redis = MagicMock()

        # Fresh heartbeat
        mock_redis.hgetall.return_value = {
            "engine_id": "whisper-cpu",
            "instance_id": "whisper-cpu-aliveinst01",
            "status": "idle",
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }

        result = is_engine_alive(mock_redis, "whisper-cpu-aliveinst01")

        assert result is True

    def test_new_instance_does_not_mask_old_instance(self):
        """Test that new instance heartbeating doesn't mask old instance's death.

        This is the key scenario: spot instance replaced with same ENGINE_ID.
        Old tasks are owned by old instance_id. New instance heartbeats under
        new instance_id. Checking old instance_id should return False.
        """
        from dalston.common.streams_sync import is_engine_alive

        mock_redis = MagicMock()

        # First call: check old instance - should return {} (no key)
        # Second call: check new instance - should return heartbeat data
        def hgetall_side_effect(key):
            if "oldinst01" in key:
                return {}  # Old instance has no heartbeat
            elif "newinst02" in key:
                return {
                    "engine_id": "whisper-cpu",
                    "instance_id": "whisper-cpu-newinst02",
                    "status": "idle",
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return {}

        mock_redis.hgetall.side_effect = hgetall_side_effect

        # Old instance should be dead
        old_alive = is_engine_alive(mock_redis, "whisper-cpu-oldinst01")
        assert old_alive is False

        # New instance should be alive
        new_alive = is_engine_alive(mock_redis, "whisper-cpu-newinst02")
        assert new_alive is True


class TestStaleEngineCleanup:
    """Tests for cleanup of stale engine registry entries."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_cleans_stale_instance_from_set(self, mock_redis, mock_settings):
        """Test cleanup removes stale instance_ids from instance set."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        # Setup: one engine with one stale instance (key doesn't exist)
        mock_redis.smembers.side_effect = [
            {"faster-whisper"},  # ENGINE_SET_KEY
            {"faster-whisper-abc123"},  # instances for faster-whisper
        ]
        mock_redis.exists.return_value = 0  # Instance key doesn't exist (TTL expired)
        mock_redis.scard.return_value = 0  # No instances remain after cleanup

        count = await sweeper._cleanup_stale_engine_entries()

        assert count == 1
        # Should remove stale instance from set
        mock_redis.srem.assert_any_call(
            "dalston:batch:engine:instances:faster-whisper",
            "faster-whisper-abc123",
        )

    @pytest.mark.asyncio
    async def test_removes_engine_id_when_no_instances_remain(
        self, mock_redis, mock_settings
    ):
        """Test cleanup removes engine_id from main set when no instances remain."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        mock_redis.smembers.side_effect = [
            {"faster-whisper"},
            {"faster-whisper-abc123"},
        ]
        mock_redis.exists.return_value = 0  # Instance key doesn't exist
        mock_redis.scard.return_value = 0  # No instances remain

        await sweeper._cleanup_stale_engine_entries()

        # Should remove engine_id from main set
        mock_redis.srem.assert_any_call("dalston:batch:engines", "faster-whisper")
        # Should delete the empty instances set
        mock_redis.delete.assert_called_once_with(
            "dalston:batch:engine:instances:faster-whisper"
        )

    @pytest.mark.asyncio
    async def test_keeps_engine_id_when_healthy_instances_exist(
        self, mock_redis, mock_settings
    ):
        """Test cleanup keeps engine_id when at least one instance is healthy."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        # Use a list to ensure deterministic ordering
        mock_redis.smembers.side_effect = [
            {"faster-whisper"},
            # Use a list internally to force iteration order
            ["faster-whisper-stale", "faster-whisper-healthy"],
        ]

        # Configure exists() to return 0 (stale) for first instance, 1 (healthy) for second
        def exists_side_effect(key):
            if "stale" in key:
                return 0  # Does not exist (stale)
            return 1  # Exists (healthy)

        mock_redis.exists.side_effect = exists_side_effect
        mock_redis.scard.return_value = 1  # One instance remains after cleanup

        count = await sweeper._cleanup_stale_engine_entries()

        # Should only remove stale instance
        assert count == 1
        mock_redis.srem.assert_called_once_with(
            "dalston:batch:engine:instances:faster-whisper",
            "faster-whisper-stale",
        )
        # Should NOT remove engine_id from main set (still has healthy instance)
        srem_calls = mock_redis.srem.call_args_list
        engine_set_calls = [c for c in srem_calls if "dalston:batch:engines" in str(c)]
        assert len(engine_set_calls) == 0

    @pytest.mark.asyncio
    async def test_no_cleanup_when_all_instances_healthy(
        self, mock_redis, mock_settings
    ):
        """Test no cleanup when all instances are healthy."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=mock_settings,
        )

        mock_redis.smembers.side_effect = [
            {"faster-whisper"},
            {"faster-whisper-abc123"},
        ]
        mock_redis.exists.return_value = 1  # Instance key exists (healthy)
        mock_redis.scard.return_value = 1  # Instance count unchanged

        count = await sweeper._cleanup_stale_engine_entries()

        assert count == 0
        mock_redis.srem.assert_not_called()


class TestHandleTaskCompletedReplaySafety:
    """Tests for handle_task_completed replay safety."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_replay_still_queues_dependents_when_already_completed(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay of task.completed still queues dependents.

        This covers the crash scenario where:
        1. Task completes → status set to COMPLETED, DB committed
        2. Crash before queueing dependents or checking job completion
        3. Replay receives same task.completed event
        4. Handler should still proceed to queue dependents and check job completion
        """
        from dalston.orchestrator.handlers import handle_task_completed

        task_id = uuid4()
        job_id = uuid4()
        dependent_task_id = uuid4()

        # Task that's already COMPLETED (from first handling before crash)
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.COMPLETED.value  # Already completed
        mock_task.engine_id = "faster-whisper"

        # Dependent task that should be queued
        mock_dependent = MagicMock()
        mock_dependent.id = dependent_task_id
        mock_dependent.job_id = job_id
        mock_dependent.stage = "merge"
        mock_dependent.status = TaskStatus.PENDING.value
        mock_dependent.dependencies = [task_id]
        mock_dependent.input_uri = None
        mock_dependent.engine_id = "final-merger"
        mock_dependent.config = {}
        mock_dependent.output_uri = "s3://bucket/output.json"
        mock_dependent.required = True
        mock_dependent.retries = 0
        mock_dependent.max_retries = 3

        # Mock job (not cancelling)
        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.status = "running"
        mock_job.audio_uri = "s3://bucket/input.wav"

        def get_side_effect(model, id):
            if id == task_id:
                return mock_task
            if id == job_id:
                return mock_job
            return None

        mock_db_session.get = AsyncMock(side_effect=get_side_effect)

        # Mock the all_tasks query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task, mock_dependent]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        # Mock the atomic UPDATE for transitioning PENDING → READY
        mock_update_result = MagicMock()
        mock_update_result.scalar_one_or_none.return_value = dependent_task_id
        mock_db_session.execute = AsyncMock(return_value=mock_update_result)

        with (
            patch("dalston.orchestrator.handlers.queue_task", new_callable=AsyncMock),
            patch(
                "dalston.orchestrator.handlers._check_job_completion",
                new_callable=AsyncMock,
            ) as mock_check_completion,
            patch(
                "dalston.orchestrator.handlers._gather_previous_outputs",
                new_callable=AsyncMock,
                return_value={"transcribe": {"segments": []}},
            ),
        ):
            await handle_task_completed(
                task_id=task_id,
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Should still check job completion even though task was already COMPLETED
            mock_check_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_does_not_double_count_metrics(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay doesn't double-count completion metrics."""
        from dalston.orchestrator.handlers import handle_task_completed

        task_id = uuid4()
        job_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.COMPLETED.value  # Already completed
        mock_task.engine_id = "faster-whisper"

        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.status = "running"

        def get_side_effect(model, id):
            if id == task_id:
                return mock_task
            if id == job_id:
                return mock_job
            return None

        mock_db_session.get = AsyncMock(side_effect=get_side_effect)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        with (
            patch(
                "dalston.orchestrator.handlers._check_job_completion",
                new_callable=AsyncMock,
            ),
            patch("dalston.metrics.inc_orchestrator_tasks_completed") as mock_metric,
        ):
            await handle_task_completed(
                task_id=task_id,
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Metrics should NOT be incremented on replay (task already terminal)
            mock_metric.assert_not_called()


class TestHandleTaskFailedIdempotency:
    """Tests for handle_task_failed idempotency with duplicate events."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_replay_ready_task_requeues_without_increment(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay of READY task re-enqueues without incrementing retries.

        This covers the crash scenario where:
        1. Task fails → retries incremented, status set to READY, DB committed
        2. Crash before requeue
        3. Replay receives same task.failed event
        4. Task is now READY - should requeue WITHOUT incrementing retries again
        """
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()

        # Mock task that's already in READY state (retry committed but not queued)
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = (
            TaskStatus.READY.value
        )  # Already set to READY by first failure
        mock_task.retries = 1  # Already incremented by first failure
        mock_task.max_retries = 3
        mock_task.dependencies = []
        mock_task.engine_id = "faster-whisper"
        mock_task.config = {}
        mock_task.input_uri = "s3://bucket/input.wav"
        mock_task.output_uri = "s3://bucket/output.json"
        mock_task.required = True
        mock_task.error = "Previous error"

        mock_db_session.get = AsyncMock(return_value=mock_task)

        # Mock the all_tasks query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "dalston.orchestrator.handlers.queue_task", new_callable=AsyncMock
        ) as mock_queue_task:
            await handle_task_failed(
                task_id=task_id,
                error="Replay error message",
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Retries should NOT be incremented again
            assert mock_task.retries == 1
            # Task should be re-enqueued
            mock_queue_task.assert_called_once()
            queue_kwargs = mock_queue_task.call_args.kwargs
            assert (
                queue_kwargs["enqueue_idempotency_key"]
                == f"dalston:task:retry-enqueue:{task_id}:1"
            )

    @pytest.mark.asyncio
    async def test_replay_skipped_task_calls_handle_task_completed(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay of SKIPPED task proceeds to handle_task_completed.

        This covers the crash scenario where:
        1. Optional task fails → status set to SKIPPED, DB committed
        2. Crash before handle_task_completed() side effects
        3. Replay receives same task.failed event
        4. Handler should proceed to call handle_task_completed for dependency processing
        """
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "diarize"
        mock_task.status = TaskStatus.SKIPPED.value  # Already skipped
        mock_task.error = "Previous error"
        mock_task.required = False

        mock_db_session.get = AsyncMock(return_value=mock_task)

        with patch(
            "dalston.orchestrator.handlers.handle_task_completed",
            new_callable=AsyncMock,
        ) as mock_handle_completed:
            await handle_task_failed(
                task_id=task_id,
                error="Replay error",
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Should call handle_task_completed for dependency side effects
            mock_handle_completed.assert_called_once_with(
                task_id, mock_db_session, mock_redis, mock_settings, mock_registry
            )

    @pytest.mark.asyncio
    async def test_replay_failed_task_fails_job(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay of FAILED task proceeds to fail job and publish webhook.

        This covers the crash scenario where:
        1. Required task fails → status set to FAILED, DB committed
        2. Crash before job fail/webhook side effects
        3. Replay receives same task.failed event
        4. Handler should proceed to fail job and publish webhook
        """
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()
        tenant_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.FAILED.value  # Already failed
        mock_task.error = "Previous error"
        mock_task.required = True

        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.tenant_id = tenant_id
        mock_job.status = "running"  # Job not yet failed

        def get_side_effect(model, id):
            if id == task_id:
                return mock_task
            if id == job_id:
                return mock_job
            return None

        mock_db_session.get = AsyncMock(side_effect=get_side_effect)

        with (
            patch(
                "dalston.orchestrator.handlers._decrement_concurrent_jobs",
                new_callable=AsyncMock,
            ) as mock_decrement,
            patch(
                "dalston.orchestrator.handlers.publish_job_failed",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await handle_task_failed(
                task_id=task_id,
                error="Replay error",
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Job should be failed
            assert mock_job.status == "failed"
            # Decrement should be called
            mock_decrement.assert_called_once()
            # Webhook should be published
            mock_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_replay_failed_task_runs_side_effects_even_if_job_already_failed(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that replay always runs side effects (at-least-once delivery).

        This covers the crash scenario where:
        1. Task fails → status FAILED, job status FAILED committed
        2. Crash before decrement/webhook
        3. Replay sees job already FAILED but must still run side effects
        4. Side effects are idempotent/deduplicated at delivery layer
        """
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()
        tenant_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.FAILED.value
        mock_task.error = "Previous error"

        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.tenant_id = tenant_id
        mock_job.status = "failed"  # Job already failed
        mock_job.error = "Task transcribe failed: Previous error"

        def get_side_effect(model, id):
            if id == task_id:
                return mock_task
            if id == job_id:
                return mock_job
            return None

        mock_db_session.get = AsyncMock(side_effect=get_side_effect)

        with (
            patch(
                "dalston.orchestrator.handlers._decrement_concurrent_jobs",
                new_callable=AsyncMock,
            ) as mock_decrement,
            patch(
                "dalston.orchestrator.handlers.publish_job_failed",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await handle_task_failed(
                task_id=task_id,
                error="Replay error",
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

            # Side effects SHOULD run (at-least-once, idempotent/dedupe at delivery)
            mock_decrement.assert_called_once()
            mock_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_failure_for_pending_task(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that failure events for PENDING tasks are ignored.

        PENDING tasks haven't been picked up yet, so they shouldn't receive
        failed events. This is a safeguard against out-of-order events.
        """
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.PENDING.value
        mock_task.retries = 0
        mock_task.max_retries = 3

        mock_db_session.get = AsyncMock(return_value=mock_task)

        await handle_task_failed(
            task_id=task_id,
            error="Error for pending task",
            db=mock_db_session,
            redis=mock_redis,
            settings=mock_settings,
            registry=mock_registry,
        )

        # Should not touch the task
        assert mock_task.retries == 0
        assert mock_task.status == TaskStatus.PENDING.value
        mock_db_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_failure_for_running_task(
        self, mock_redis, mock_db_session, mock_settings, mock_registry
    ):
        """Test that failure events ARE processed for RUNNING tasks."""
        from dalston.orchestrator.handlers import handle_task_failed

        task_id = uuid4()
        job_id = uuid4()

        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe"
        mock_task.status = TaskStatus.RUNNING.value  # Normal state for failure
        mock_task.retries = 0
        mock_task.max_retries = 3
        mock_task.dependencies = []
        # Additional fields needed for Pydantic Task validation
        mock_task.engine_id = "faster-whisper"
        mock_task.config = {}
        mock_task.input_uri = "s3://bucket/input.wav"
        mock_task.output_uri = "s3://bucket/output.json"
        mock_task.required = True
        mock_task.error = None

        mock_db_session.get = AsyncMock(return_value=mock_task)

        # Mock the all_tasks query for retry path
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "dalston.orchestrator.handlers.queue_task", new_callable=AsyncMock
        ) as mock_queue_task:
            await handle_task_failed(
                task_id=task_id,
                error="Task failed",
                db=mock_db_session,
                redis=mock_redis,
                settings=mock_settings,
                registry=mock_registry,
            )

        # Should increment retries and update status
        assert mock_task.retries == 1
        assert mock_task.status == TaskStatus.READY.value
        assert (
            mock_queue_task.call_args.kwargs["enqueue_idempotency_key"]
            == f"dalston:task:retry-enqueue:{task_id}:1"
        )
        mock_db_session.commit.assert_called()


class TestBatchEngineRegistry:
    """Tests for BatchEngineRegistry with instance_id support."""

    def test_register_uses_instance_id_for_key(self):
        """Test that registration uses instance_id for Redis key."""
        from dalston.engine_sdk.registry import BatchEngineInfo, BatchEngineRegistry

        mock_redis = MagicMock()

        with patch.object(BatchEngineRegistry, "_get_redis", return_value=mock_redis):
            registry = BatchEngineRegistry("redis://localhost:6379")

            info = BatchEngineInfo(
                engine_id="whisper-cpu",
                instance_id="whisper-cpu-abc123def456",
                stage="transcribe",
                queue_name="dalston:queue:whisper-cpu",
            )

            registry.register(info)

            # Verify Redis key includes instance_id
            mock_redis.hset.assert_called_once()
            call_args = mock_redis.hset.call_args
            key = call_args[0][0]
            assert "whisper-cpu-abc123def456" in key
            assert "dalston:batch:engine:" in key

    def test_heartbeat_uses_instance_id_for_key(self):
        """Test that heartbeat uses instance_id for Redis key."""
        from dalston.engine_sdk.registry import BatchEngineInfo, BatchEngineRegistry

        mock_redis = MagicMock()
        mock_redis.hget.return_value = "whisper-cpu"  # Key exists

        with patch.object(BatchEngineRegistry, "_get_redis", return_value=mock_redis):
            registry = BatchEngineRegistry("redis://localhost:6379")

            # First register to populate cache
            info = BatchEngineInfo(
                engine_id="whisper-cpu",
                instance_id="whisper-cpu-abc123def456",
                stage="transcribe",
                queue_name="dalston:queue:whisper-cpu",
            )
            registry.register(info)
            mock_redis.reset_mock()

            # Now send heartbeat
            registry.heartbeat(
                instance_id="whisper-cpu-abc123def456",
                status="idle",
            )

            # Verify key includes instance_id
            mock_redis.hset.assert_called_once()
            mock_redis.expire.assert_called_once()
            call_args = mock_redis.expire.call_args
            key = call_args[0][0]
            assert "whisper-cpu-abc123def456" in key
