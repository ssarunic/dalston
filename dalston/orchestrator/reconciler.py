"""Reconciliation sweeper for Redis Streams and PostgreSQL consistency.

Periodically scans for discrepancies between:
- Tasks marked RUNNING in PostgreSQL but not in any Stream's PEL
- Tasks in the PEL that are not RUNNING in PostgreSQL

This handles edge cases where:
- Redis loses data (crash without persistence)
- Orchestrator crashes between Stream and DB operations
- Tasks get stuck in inconsistent states

Uses leader election (shared with scanner) to ensure only one instance runs.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_event
from dalston.common.models import TaskStatus
from dalston.common.s3 import get_s3_client
from dalston.common.streams import (
    STREAM_PREFIX,
    ack_task,
    add_task,
    discover_streams,
    get_pending,
    is_engine_alive,
)
from dalston.common.streams_types import CONSUMER_GROUP, PendingTask
from dalston.config import Settings
from dalston.db.models import TaskModel
from dalston.orchestrator.registry import (
    ENGINE_INSTANCES_PREFIX,
    ENGINE_KEY_PREFIX,
    ENGINE_SET_KEY,
)

logger = structlog.get_logger()

# Reconciliation configuration
DEFAULT_RECONCILE_INTERVAL_SECONDS = 300  # 5 minutes
ORPHAN_THRESHOLD_SECONDS = 600  # 10 minutes - only reconcile tasks older than this
READY_TASK_RECOVERY_THRESHOLD_SECONDS = 120

# Leader election (separate from scanner to allow independent operation)
RECONCILER_LOCK_KEY = "dalston:reconciler:leader"
RECONCILER_LOCK_TTL_SECONDS = 120


class ReconciliationSweeper:
    """Background sweeper for reconciling Streams and DB state.

    Runs periodically to detect and fix inconsistencies:

    1. Orphaned DB tasks: RUNNING in DB but not in any PEL
       - These are tasks that were claimed but the engine crashed before
         publishing task.started, or the PEL entry was lost
       - Action: Re-queue the task (mark as READY) or fail if too old

    2. Orphaned PEL entries: In PEL but not RUNNING in DB
       - These are ACKed tasks where the ACK was lost, or DB was rolled back
       - Action: ACK the entry to clean up the PEL
    """

    def __init__(
        self,
        redis: Redis,
        db_session_factory,
        settings: Settings,
        reconcile_interval_seconds: int = DEFAULT_RECONCILE_INTERVAL_SECONDS,
        instance_id: str | None = None,
    ):
        """Initialize the reconciliation sweeper.

        Args:
            redis: Async Redis client
            db_session_factory: Async context manager factory for DB sessions
            settings: Application settings
            reconcile_interval_seconds: How often to run reconciliation
            instance_id: Unique identifier for this instance
        """
        self._redis = redis
        self._db_session_factory = db_session_factory
        self._settings = settings
        self._reconcile_interval = reconcile_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._instance_id = instance_id or f"{os.uname().nodename}:{os.getpid()}"
        self._is_leader = False

    async def start(self) -> None:
        """Start the reconciliation sweeper."""
        if self._running:
            logger.warning("reconciliation_sweeper_already_running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "reconciliation_sweeper_started",
            reconcile_interval_seconds=self._reconcile_interval,
            instance_id=self._instance_id,
        )

    async def stop(self) -> None:
        """Stop the reconciliation sweeper."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._is_leader:
            await self._release_leader_lock()

        logger.info("reconciliation_sweeper_stopped", instance_id=self._instance_id)

    async def _acquire_leader_lock(self) -> bool:
        """Try to acquire the leader lock."""
        acquired = await self._redis.set(
            RECONCILER_LOCK_KEY,
            self._instance_id,
            nx=True,
            ex=RECONCILER_LOCK_TTL_SECONDS,
        )
        return acquired is not None

    async def _release_leader_lock(self) -> None:
        """Release the leader lock if we hold it."""
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self._redis.eval(script, 1, RECONCILER_LOCK_KEY, self._instance_id)
        except Exception:
            logger.debug("reconciler_lock_release_failed", exc_info=True)

    async def _run_loop(self) -> None:
        """Main reconciliation loop."""
        while self._running:
            try:
                await asyncio.sleep(self._reconcile_interval)

                if await self._acquire_leader_lock():
                    if not self._is_leader:
                        logger.info(
                            "reconciler_became_leader", instance_id=self._instance_id
                        )
                        self._is_leader = True

                    await self._reconcile()

                    await self._release_leader_lock()
                    self._is_leader = False
                else:
                    if self._is_leader:
                        logger.info(
                            "reconciler_lost_leadership", instance_id=self._instance_id
                        )
                        self._is_leader = False
                    logger.debug("reconciler_not_leader", instance_id=self._instance_id)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("reconciliation_error", exc_info=True)
                self._is_leader = False

    async def _reconcile(self) -> None:
        """Perform one reconciliation pass."""
        orphaned_db_count = 0
        orphaned_pel_count = 0
        stale_ready_count = 0
        recovered_ready_count = 0

        # Get all pending entries from all streams
        streams = await discover_streams(self._redis)
        pel_task_ids: set[str] = set()
        pel_by_stage: dict[str, set[str]] = {}
        # Track full PendingTask objects for stale READY recovery
        pel_entries_by_stage: dict[str, dict[str, PendingTask]] = {}

        for stream_key in streams:
            stage = stream_key.replace(STREAM_PREFIX, "")
            pending = await get_pending(self._redis, stage)
            pel_by_stage[stage] = set()
            pel_entries_by_stage[stage] = {}

            for entry in pending:
                pel_task_ids.add(entry.task_id)
                pel_by_stage[stage].add(entry.task_id)
                pel_entries_by_stage[stage][entry.task_id] = entry

        # Find orphaned DB tasks (RUNNING but not in PEL)
        async with self._db_session_factory() as db:
            orphaned_db_count = await self._reconcile_orphaned_db_tasks(
                db, pel_task_ids
            )

        # Find orphaned PEL entries (in PEL but task in terminal state in DB)
        async with self._db_session_factory() as db:
            orphaned_pel_count = await self._reconcile_orphaned_pel_entries(
                db, pel_by_stage
            )

        # Recover stale READY/PENDING tasks from dead engines
        async with self._db_session_factory() as db:
            stale_ready_count = await self._reconcile_stale_ready_tasks(
                db, pel_entries_by_stage
            )

        # Recover READY tasks whose stream message was consumed/ACKed but task
        # state never transitioned out of READY.
        async with self._db_session_factory() as db:
            recovered_ready_count = (
                await self._reconcile_ready_tasks_with_consumed_messages(db)
            )

        # Cleanup stale engine registry entries (instances that crashed without unregister)
        stale_engine_count = await self._cleanup_stale_engine_entries()

        if (
            orphaned_db_count > 0
            or orphaned_pel_count > 0
            or stale_ready_count > 0
            or recovered_ready_count > 0
            or stale_engine_count > 0
        ):
            logger.info(
                "reconciliation_complete",
                orphaned_db_tasks=orphaned_db_count,
                orphaned_pel_entries=orphaned_pel_count,
                stale_ready_tasks=stale_ready_count,
                recovered_ready_tasks=recovered_ready_count,
                stale_engine_entries=stale_engine_count,
            )

    async def _check_output_exists_in_s3(
        self, job_id: str, task_id: str
    ) -> bool | None:
        """Check if task output exists in S3.

        Engine uploads output to a predictable path:
        s3://{bucket}/jobs/{job_id}/tasks/{task_id}/output.json

        Args:
            job_id: Job UUID as string
            task_id: Task UUID as string

        Returns:
            True if output file exists in S3
            False if output file definitely does not exist (404/NoSuchKey)
            None if there was a transient error (network, auth, etc.)
        """
        key = f"jobs/{job_id}/tasks/{task_id}/output.json"

        try:
            async with get_s3_client(self._settings) as s3:
                await s3.head_object(Bucket=self._settings.s3_bucket, Key=key)
                return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            # 404 and NoSuchKey definitively mean object doesn't exist
            if error_code in ("404", "NoSuchKey"):
                return False
            # Other errors (auth, network, throttling) are transient - return None
            logger.warning(
                "s3_check_output_transient_error",
                job_id=job_id,
                task_id=task_id,
                error_code=error_code,
                error=str(e),
            )
            return None
        except Exception as e:
            # Network errors, timeouts, etc. are transient
            logger.warning(
                "s3_check_output_transient_error",
                job_id=job_id,
                task_id=task_id,
                error=str(e),
            )
            return None

    async def _reconcile_orphaned_db_tasks(
        self, db: AsyncSession, pel_task_ids: set[str]
    ) -> int:
        """Find and fix tasks that are RUNNING in DB but not in PEL.

        These tasks were claimed but either:
        - The engine crashed before processing started
        - Redis lost the PEL entry

        Action: Mark as READY to re-queue, or FAILED if too old.
        """
        # Find RUNNING tasks older than threshold
        threshold = datetime.now(UTC) - timedelta(seconds=ORPHAN_THRESHOLD_SECONDS)

        result = await db.execute(
            select(TaskModel)
            .where(TaskModel.status == TaskStatus.RUNNING.value)
            .where(TaskModel.started_at < threshold)
        )
        running_tasks = list(result.scalars().all())

        orphaned_count = 0

        for task in running_tasks:
            task_id_str = str(task.id)
            job_id_str = str(task.job_id)

            if task_id_str not in pel_task_ids:
                # Task is RUNNING in DB but not in any PEL - orphaned
                # This can happen if:
                # 1. Engine crashed before publishing task.started
                # 2. Task completed but task.completed event was lost
                # 3. PEL entry was lost (Redis crash)

                # Check if output exists in S3 - if so, task completed successfully
                # but the task.completed event was lost
                # Note: output_uri is not set in DB, so we must check S3 directly
                # Returns: True (exists), False (not found), None (transient error)
                output_exists = await self._check_output_exists_in_s3(
                    job_id_str, task_id_str
                )

                if output_exists is None:
                    # Transient S3 error - skip this task, will retry next cycle
                    logger.warning(
                        "orphaned_db_task_skipped_s3_error",
                        task_id=task_id_str,
                        stage=task.stage,
                        note="will_retry_next_cycle",
                    )
                    continue

                if output_exists:
                    logger.info(
                        "orphaned_db_task_recovered_as_completed",
                        task_id=task_id_str,
                        stage=task.stage,
                        note="output_found_in_s3",
                    )
                    task.status = TaskStatus.COMPLETED.value
                    task.completed_at = datetime.now(UTC)
                    orphaned_count += 1

                    # Publish completion event
                    await publish_event(
                        self._redis,
                        "task.completed",
                        {
                            "task_id": task_id_str,
                            "job_id": job_id_str,
                            "stage": task.stage,
                            "reconciler_action": "recovered_completed",
                        },
                    )
                else:
                    # output_exists is False - definitively no output
                    logger.warning(
                        "orphaned_db_task_found",
                        task_id=task_id_str,
                        stage=task.stage,
                        started_at=task.started_at.isoformat()
                        if task.started_at
                        else None,
                    )

                    # No output in S3 - mark as FAILED (safer than re-queuing)
                    task.status = TaskStatus.FAILED.value
                    task.error = (
                        "Task orphaned: not found in Redis PEL during reconciliation"
                    )
                    task.completed_at = datetime.now(UTC)
                    orphaned_count += 1

                    # Publish failure event
                    await publish_event(
                        self._redis,
                        "task.failed",
                        {
                            "task_id": task_id_str,
                            "job_id": job_id_str,
                            "error": task.error,
                            "reconciler_action": "marked_failed",
                        },
                    )

        if orphaned_count > 0:
            await db.commit()

        return orphaned_count

    async def _reconcile_orphaned_pel_entries(
        self, db: AsyncSession, pel_by_stage: dict[str, set[str]]
    ) -> int:
        """Find and fix PEL entries that are not RUNNING in DB.

        These entries exist in the PEL but the DB task is not RUNNING:
        - Task was ACKed but ACK was lost
        - DB transaction was rolled back
        - Task was marked FAILED/COMPLETED but PEL wasn't cleaned

        Action: ACK the entry to clean up the PEL.
        """
        orphaned_count = 0

        for stage, task_ids in pel_by_stage.items():
            if not task_ids:
                continue

            # Convert to UUIDs for DB query
            task_uuids = []
            for tid in task_ids:
                try:
                    task_uuids.append(UUID(tid))
                except ValueError:
                    continue

            if not task_uuids:
                continue

            # Get task statuses from DB
            result = await db.execute(
                select(TaskModel.id, TaskModel.status).where(
                    TaskModel.id.in_(task_uuids)
                )
            )
            db_tasks = {str(row[0]): row[1] for row in result.all()}

            # Only ACK PEL entries for tasks in TERMINAL statuses
            # Don't ACK RUNNING (normal), READY (race condition), or PENDING (shouldn't happen)
            # This prevents losing tasks that were just claimed but not yet marked RUNNING
            terminal_statuses = {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.SKIPPED.value,
                TaskStatus.CANCELLED.value,
            }

            # Find PEL entries where DB task is in terminal state
            for task_id in task_ids:
                db_status = db_tasks.get(task_id)

                # Skip if task doesn't exist in DB (will be handled by scanner)
                if db_status is None:
                    continue

                # Only ACK if task is in a terminal state
                # This prevents the race condition where a task was just claimed
                # (in PEL) but task.started hasn't been processed yet (still READY)
                if db_status not in terminal_statuses:
                    continue

                # PEL entry exists but task is in terminal state - safe to ACK
                logger.warning(
                    "orphaned_pel_entry_found",
                    task_id=task_id,
                    stage=stage,
                    db_status=db_status,
                )

                # Get the message ID to ACK
                pending = await get_pending(self._redis, stage)
                for entry in pending:
                    if entry.task_id == task_id:
                        await ack_task(self._redis, stage, entry.message_id)
                        logger.info(
                            "orphaned_pel_entry_acked",
                            task_id=task_id,
                            message_id=entry.message_id,
                        )
                        orphaned_count += 1
                        break

        return orphaned_count

    async def _reconcile_stale_ready_tasks(
        self, db: AsyncSession, pel_entries_by_stage: dict[str, dict[str, PendingTask]]
    ) -> int:
        """Recover READY/PENDING tasks with stale PEL entries from dead engines.

        Handles the case where:
        - Task is READY/PENDING in DB (engine claimed but task.started not processed)
        - PEL entry exists but is stale (idle > threshold)
        - Owning engine is dead (no heartbeat)

        Action: ACK old PEL entry and re-queue task to stream.
        """
        recovered_count = 0

        for stage, entries in pel_entries_by_stage.items():
            for task_id, pel_entry in entries.items():
                # Only process stale entries (idle > ORPHAN_THRESHOLD_SECONDS)
                if pel_entry.idle_ms < ORPHAN_THRESHOLD_SECONDS * 1000:
                    continue

                # Check DB status
                try:
                    task_uuid = UUID(task_id)
                except ValueError:
                    continue

                task = await db.get(TaskModel, task_uuid)
                if task is None:
                    continue

                # Only handle READY or PENDING tasks
                if task.status not in (
                    TaskStatus.READY.value,
                    TaskStatus.PENDING.value,
                ):
                    continue

                # Check if owning engine is alive
                engine_alive = await is_engine_alive(self._redis, pel_entry.consumer)

                if engine_alive:
                    # Engine is alive but task is stale - log warning, don't take action
                    # This could be a slow engine or a bug, but we don't want to interfere
                    logger.warning(
                        "stale_ready_task_engine_alive",
                        task_id=task_id,
                        stage=stage,
                        consumer=pel_entry.consumer,
                        idle_ms=pel_entry.idle_ms,
                        db_status=task.status,
                    )
                    continue

                # Engine is dead, task is stuck in READY/PENDING - re-queue
                logger.info(
                    "recovering_stale_ready_task",
                    task_id=task_id,
                    job_id=str(task.job_id),
                    stage=stage,
                    consumer=pel_entry.consumer,
                    idle_ms=pel_entry.idle_ms,
                    db_status=task.status,
                )

                # IMPORTANT: Add task FIRST, then ACK the old PEL entry.
                # This order is critical for crash safety:
                # - If crash after add but before ACK: Task in both stream and PEL,
                #   duplicate will be deduplicated when engine claims.
                # - If ACK first and crash before add: Task is lost forever.
                # Duplicates are recoverable, lost tasks are not.

                # Re-add task to stream with default timeout
                # The scheduler would normally calculate timeout from audio duration,
                # but we don't have that info here. Use a safe default.
                await add_task(
                    self._redis,
                    stage=stage,
                    task_id=task_id,
                    job_id=str(task.job_id),
                    timeout_s=600,  # 10 minute default timeout
                )

                # Now ACK old PEL entry to clear it
                await ack_task(self._redis, stage, pel_entry.message_id)

                recovered_count += 1

        return recovered_count

    async def _reconcile_ready_tasks_with_consumed_messages(
        self, db: AsyncSession
    ) -> int:
        """Re-queue READY tasks whose original stream message was already consumed.

        This recovers tasks that can be left in READY when their queue message
        was acknowledged but no durable state-transition event was applied.
        """
        threshold = datetime.now(UTC) - timedelta(
            seconds=READY_TASK_RECOVERY_THRESHOLD_SECONDS
        )
        result = await db.execute(
            select(TaskModel).where(TaskModel.status == TaskStatus.READY.value)
        )
        ready_tasks = list(result.scalars().all())
        recovered_count = 0
        group_last_delivered: dict[str, str | None] = {}
        now = datetime.now(UTC)

        for task in ready_tasks:
            task_id = str(task.id)
            metadata_key = f"dalston:task:{task_id}"
            metadata = await self._redis.hgetall(metadata_key)
            if not metadata:
                continue

            stream_message_id = metadata.get("stream_message_id")
            if not stream_message_id:
                continue

            enqueued_at_str = metadata.get("enqueued_at")
            if enqueued_at_str:
                try:
                    enqueued_at = datetime.fromisoformat(enqueued_at_str)
                    if enqueued_at.tzinfo is None:
                        enqueued_at = enqueued_at.replace(tzinfo=UTC)
                except ValueError:
                    enqueued_at = None
                if enqueued_at and enqueued_at > threshold:
                    continue

            queue_id = (
                metadata.get("queue_id")
                or metadata.get("stage")
                or metadata.get("engine_id")
                or task.engine_id
            )
            if not queue_id:
                continue

            if await self._is_stream_message_pending(queue_id, stream_message_id):
                continue

            if not await self._is_stream_message_delivered(
                queue_id, stream_message_id, group_last_delivered
            ):
                continue

            new_message_id = await add_task(
                self._redis,
                stage=queue_id,
                task_id=task_id,
                job_id=str(task.job_id),
                timeout_s=600,
            )
            await self._redis.hset(
                metadata_key,
                mapping={
                    "queue_id": queue_id,
                    "stream_message_id": new_message_id,
                    "reconciler_requeued_at": now.isoformat(),
                    "reconciler_previous_message_id": stream_message_id,
                },
            )
            recovered_count += 1
            logger.warning(
                "requeued_ready_task_with_consumed_message",
                task_id=task_id,
                job_id=str(task.job_id),
                queue_id=queue_id,
                old_message_id=stream_message_id,
                new_message_id=new_message_id,
            )

        return recovered_count

    async def _is_stream_message_pending(self, queue_id: str, message_id: str) -> bool:
        """Return True if a message is currently in the PEL."""
        try:
            pending = await self._redis.xpending_range(
                f"{STREAM_PREFIX}{queue_id}",
                CONSUMER_GROUP,
                min=message_id,
                max=message_id,
                count=1,
            )
            return bool(pending)
        except Exception:
            return False

    async def _is_stream_message_delivered(
        self,
        queue_id: str,
        message_id: str,
        cache: dict[str, str | None],
    ) -> bool:
        """Return True if message_id is at/before group's last-delivered ID."""
        if queue_id not in cache:
            cache[queue_id] = await self._group_last_delivered_id(queue_id)
        last_delivered = cache[queue_id]
        if not last_delivered:
            return False
        return self._stream_id_lte(message_id, last_delivered)

    async def _group_last_delivered_id(self, queue_id: str) -> str | None:
        """Get consumer group's last delivered stream ID for a queue."""
        try:
            groups = await self._redis.xinfo_groups(f"{STREAM_PREFIX}{queue_id}")
        except Exception:
            return None

        for group in groups:
            name = group.get("name")
            if isinstance(name, bytes):
                name = name.decode()
            if name != CONSUMER_GROUP:
                continue
            last_delivered = group.get("last-delivered-id")
            if isinstance(last_delivered, bytes):
                last_delivered = last_delivered.decode()
            return str(last_delivered) if last_delivered else None
        return None

    @staticmethod
    def _stream_id_lte(left: str, right: str) -> bool:
        """Compare Redis stream IDs (ms-seq) safely."""
        return ReconciliationSweeper._stream_id_to_tuple(
            left
        ) <= ReconciliationSweeper._stream_id_to_tuple(right)

    @staticmethod
    def _stream_id_to_tuple(stream_id: str) -> tuple[int, int]:
        try:
            milliseconds, sequence = stream_id.split("-", 1)
            return int(milliseconds), int(sequence)
        except Exception:
            return (0, 0)

    async def _cleanup_stale_engine_entries(self) -> int:
        """Cleanup stale engine registry entries.

        Handles the case where engines crash without calling unregister():
        - Engine's Redis hash key expires (TTL)
        - But ENGINE_INSTANCES_PREFIX{engine_id} set still contains the instance_id
        - And ENGINE_SET_KEY still contains the engine_id

        Action:
        1. For each engine_id in ENGINE_SET_KEY, check its instances
        2. Remove instance_ids from instance set if their hash key doesn't exist
        3. Remove engine_id from ENGINE_SET_KEY if it has no remaining instances
        """
        cleaned_count = 0

        # Get all logical engine IDs
        engine_ids = await self._redis.smembers(ENGINE_SET_KEY)

        for engine_id in engine_ids:
            instances_key = f"{ENGINE_INSTANCES_PREFIX}{engine_id}"
            instance_ids = await self._redis.smembers(instances_key)

            # Check each instance
            stale_instances = []
            for instance_id in instance_ids:
                engine_key = f"{ENGINE_KEY_PREFIX}{instance_id}"
                # Check if the hash key exists (TTL not expired)
                exists = await self._redis.exists(engine_key)
                if not exists:
                    stale_instances.append(instance_id)

            # Remove stale instances from the set
            for instance_id in stale_instances:
                await self._redis.srem(instances_key, instance_id)
                cleaned_count += 1
                logger.info(
                    "cleaned_stale_engine_instance",
                    engine_id=engine_id,
                    instance_id=instance_id,
                )

            # If no instances remain, remove the engine_id from the main set
            remaining = await self._redis.scard(instances_key)
            if remaining == 0:
                await self._redis.srem(ENGINE_SET_KEY, engine_id)
                # Also delete the now-empty instances set
                await self._redis.delete(instances_key)
                logger.info(
                    "cleaned_stale_engine_type",
                    engine_id=engine_id,
                    reason="no_remaining_instances",
                )

        return cleaned_count
