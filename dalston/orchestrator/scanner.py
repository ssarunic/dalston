"""Stale task scanner for Redis Streams recovery.

Periodically scans all task streams for stale tasks and handles them:
1. Tasks owned by dead engines → fail immediately
2. Tasks that have exceeded their timeout → fail with timeout error

This scanner runs as a background task in the orchestrator and provides
crash recovery for the streaming task queue system (M33).

Uses Redis-based leader election to ensure only one orchestrator instance
runs the scan at a time (safe for multi-instance deployments).
"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select

import dalston.metrics
from dalston.common.events import publish_event
from dalston.common.models import TaskStatus
from dalston.common.streams import (
    STREAM_PREFIX,
    discover_streams,
    get_pending,
    is_engine_alive,
)
from dalston.common.streams_types import CONSUMER_GROUP, WAITING_ENGINE_TASKS_KEY
from dalston.config import Settings
from dalston.db.models import TaskModel

logger = structlog.get_logger()

# Scanner configuration
DEFAULT_SCAN_INTERVAL_SECONDS = 60  # How often to scan for stale tasks
STALE_THRESHOLD_MS = 10 * 60 * 1000  # 10 minutes - same as engine SDK

# Leader election configuration
LEADER_LOCK_KEY = "dalston:scanner:leader"
LEADER_LOCK_TTL_SECONDS = 120  # Lock TTL (2x scan interval for safety)


class StaleTaskScanner:
    """Background scanner for detecting and recovering stale tasks.

    Scans Redis Streams' Pending Entries List (PEL) for tasks that:
    1. Are owned by engines that have stopped heartbeating (dead engines)
    2. Have exceeded their timeout_at time

    For each stale task found, the scanner:
    1. Marks the task as FAILED in the database
    2. Publishes a task.failed event for the orchestrator to handle

    The scanner uses leader election (when enabled) to ensure only one
    orchestrator instance runs the scan at a time.
    """

    def __init__(
        self,
        redis: Redis,
        db_session_factory,
        settings: Settings,
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
        instance_id: str | None = None,
    ):
        """Initialize the stale task scanner.

        Args:
            redis: Async Redis client (shared with orchestrator)
            db_session_factory: Async context manager factory for database sessions
            settings: Application settings
            scan_interval_seconds: How often to scan for stale tasks
            instance_id: Unique identifier for this scanner instance (default: hostname:pid)
        """
        self._redis = redis
        self._db_session_factory = db_session_factory
        self._settings = settings
        self._scan_interval = scan_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._instance_id = instance_id or f"{os.uname().nodename}:{os.getpid()}"
        self._is_leader = False

    async def start(self) -> None:
        """Start the scanner background task."""
        if self._running:
            logger.warning("stale_task_scanner_already_running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "stale_task_scanner_started",
            scan_interval_seconds=self._scan_interval,
            stale_threshold_ms=STALE_THRESHOLD_MS,
            instance_id=self._instance_id,
        )

    async def stop(self) -> None:
        """Stop the scanner."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Release leader lock if we hold it
        if self._is_leader:
            await self._release_leader_lock()

        logger.info("stale_task_scanner_stopped", instance_id=self._instance_id)

    async def _acquire_leader_lock(self) -> bool:
        """Try to acquire the leader lock.

        Uses Redis SET NX EX for atomic lock acquisition with TTL.
        The lock value contains this instance's ID for debugging.

        Returns:
            True if lock acquired (we are the leader), False otherwise
        """
        acquired = await self._redis.set(
            LEADER_LOCK_KEY,
            self._instance_id,
            nx=True,
            ex=LEADER_LOCK_TTL_SECONDS,
        )
        return acquired is not None

    async def _release_leader_lock(self) -> None:
        """Release the leader lock if we hold it.

        Only releases if the lock value matches our instance ID
        to avoid releasing another instance's lock.
        """
        # Use a Lua script for atomic check-and-delete
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self._redis.eval(script, 1, LEADER_LOCK_KEY, self._instance_id)
        except Exception:
            # Best effort - lock will expire anyway
            logger.debug("leader_lock_release_failed", exc_info=True)

    async def _extend_leader_lock(self) -> bool:
        """Extend the leader lock TTL while scanning.

        Only extends if we still hold the lock.

        Returns:
            True if lock extended, False if we lost leadership
        """
        # Use a Lua script for atomic check-and-extend
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(
                script, 1, LEADER_LOCK_KEY, self._instance_id, LEADER_LOCK_TTL_SECONDS
            )
            return result == 1
        except Exception:
            logger.debug("leader_lock_extend_failed", exc_info=True)
            return False

    async def _run_loop(self) -> None:
        """Main scanner loop with leader election."""
        while self._running:
            try:
                await asyncio.sleep(self._scan_interval)

                # Try to become leader
                if await self._acquire_leader_lock():
                    if not self._is_leader:
                        logger.info(
                            "scanner_became_leader", instance_id=self._instance_id
                        )
                        self._is_leader = True

                    # Run the scan as leader
                    await self._scan()

                    # Release lock after scan (allow other instances to take over)
                    await self._release_leader_lock()
                    self._is_leader = False
                else:
                    # Not the leader this round
                    if self._is_leader:
                        logger.info(
                            "scanner_lost_leadership", instance_id=self._instance_id
                        )
                        self._is_leader = False
                    logger.debug("scanner_not_leader", instance_id=self._instance_id)
                    dalston.metrics.inc_orchestrator_scanner_scans("skipped_not_leader")

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("stale_task_scan_error", exc_info=True)
                dalston.metrics.inc_orchestrator_scanner_scans("error")
                # Continue running despite errors
                self._is_leader = False

    async def _scan(self) -> None:
        """Perform one scan of all streams for stale tasks."""
        # Discover all task streams
        streams = await discover_streams(self._redis)

        total_stale = 0
        total_failed = 0

        for stream_key in streams:
            # Extract queue_id from stream key (dalston:stream:{queue_id})
            queue_id = stream_key.replace(STREAM_PREFIX, "")

            stale_count, failed_count = await self._scan_stream(queue_id)
            total_stale += stale_count
            total_failed += failed_count

        wait_timeout_failed = await self._scan_waiting_engine_timeouts()
        total_failed += wait_timeout_failed

        if total_stale > 0 or wait_timeout_failed > 0:
            logger.info(
                "stale_task_scan_complete",
                streams_scanned=len(streams),
                stale_tasks_found=total_stale,
                tasks_failed=total_failed,
                waiting_engine_timeouts=wait_timeout_failed,
            )

        dalston.metrics.inc_orchestrator_scanner_scans("success")

    async def _scan_stream(self, queue_id: str) -> tuple[int, int]:
        """Scan a single stream for stale tasks.

        Args:
            queue_id: Queue identifier (typically engine_id)

        Returns:
            Tuple of (stale_tasks_found, tasks_failed)
        """
        pending = await get_pending(self._redis, queue_id)

        if not pending:
            return 0, 0

        stale_count = 0
        failed_count = 0
        now = datetime.now(UTC)

        for task in pending:
            # Check if task is stale (idle > threshold)
            if task.idle_ms < STALE_THRESHOLD_MS:
                continue

            stale_count += 1

            # Check if owning engine is dead
            engine_alive = await is_engine_alive(self._redis, task.consumer)

            if not engine_alive:
                # Engine is dead - fail the task immediately
                success = await self._fail_task(
                    task_id=task.task_id,
                    queue_id=queue_id,
                    error=f"Engine '{task.consumer}' stopped heartbeating while processing task",
                    reason="engine_dead",
                )
                if success:
                    failed_count += 1
            else:
                # Engine is alive - check if task has timed out
                # Get the timeout_at from the stream message
                timed_out = await self._check_task_timeout(
                    queue_id, task.message_id, now
                )
                if timed_out:
                    success = await self._fail_task(
                        task_id=task.task_id,
                        queue_id=queue_id,
                        error="Task exceeded configured timeout",
                        reason="timeout",
                    )
                    if success:
                        failed_count += 1

        return stale_count, failed_count

    async def _check_task_timeout(
        self, queue_id: str, message_id: str, now: datetime
    ) -> bool:
        """Check if a task has exceeded its timeout_at time.

        Args:
            queue_id: Queue identifier
            message_id: Redis message ID
            now: Current time

        Returns:
            True if task has timed out, False otherwise
        """
        from dalston.common.streams import _stream_key

        stream_key = _stream_key(queue_id)

        # Get message fields to check timeout_at
        messages = await self._redis.xrange(
            stream_key, min=message_id, max=message_id, count=1
        )

        if not messages:
            return False

        _, fields = messages[0]
        timeout_str = fields.get("timeout_at", "")

        if not timeout_str:
            return False

        try:
            timeout_at = datetime.fromisoformat(timeout_str)
            return now > timeout_at
        except (ValueError, TypeError):
            return False

    async def _fail_task(
        self, task_id: str, queue_id: str, error: str, reason: str
    ) -> bool:
        """Mark a task as failed in the database and publish event.

        Args:
            task_id: Task UUID string
            queue_id: Queue identifier
            error: Error message
            reason: Reason for failure (engine_dead, timeout)

        Returns:
            True if task was successfully failed, False otherwise
        """
        log = logger.bind(task_id=task_id, queue_id=queue_id, reason=reason)

        try:
            task_uuid = UUID(task_id)
        except ValueError:
            log.warning("invalid_task_id")
            return False

        async with self._db_session_factory() as db:
            # Get task from database
            result = await db.execute(
                select(TaskModel).where(TaskModel.id == task_uuid)
            )
            task = result.scalar_one_or_none()

            if task is None:
                log.warning("task_not_found_in_db")
                return False

            # Only fail tasks that are still RUNNING
            if task.status != TaskStatus.RUNNING.value:
                log.debug(
                    "task_not_running",
                    current_status=task.status,
                )
                return False

            # Update task status
            task.status = TaskStatus.FAILED.value
            task.error = error
            task.completed_at = datetime.now(UTC)
            await db.commit()

            if reason == "timeout":
                dalston.metrics.inc_orchestrator_tasks_timed_out(task.stage)

            log.info("task_marked_failed_by_scanner", error=error, stage=task.stage)

        # Publish task.failed event
        await publish_event(
            self._redis,
            "task.failed",
            {
                "task_id": task_id,
                "error": error,
                "scanner_reason": reason,
            },
        )

        return True

    async def _scan_waiting_engine_timeouts(self) -> int:
        """Fail READY/PENDING tasks that exceeded wait-for-engine deadline."""
        if (
            getattr(self._settings, "engine_unavailable_behavior", "fail_fast")
            != "wait"
        ):
            return 0

        waiting_task_ids = await self._redis.smembers(WAITING_ENGINE_TASKS_KEY)
        if not waiting_task_ids:
            return 0

        now = datetime.now(UTC)
        timed_out_count = 0

        async with self._db_session_factory() as db:
            for task_id in waiting_task_ids:
                metadata_key = f"dalston:task:{task_id}"
                metadata = await self._redis.hgetall(metadata_key)
                if not metadata:
                    await self._redis.srem(WAITING_ENGINE_TASKS_KEY, task_id)
                    continue

                if metadata.get("waiting_for_engine") != "true":
                    await self._redis.srem(WAITING_ENGINE_TASKS_KEY, task_id)
                    continue

                deadline_str = metadata.get("wait_deadline_at")
                if not deadline_str:
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                try:
                    wait_deadline = datetime.fromisoformat(deadline_str)
                except (TypeError, ValueError):
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                if now <= wait_deadline:
                    continue

                try:
                    task_uuid = UUID(task_id)
                except ValueError:
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                task = await db.get(TaskModel, task_uuid)
                if task is None:
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                # Task already moved on - no timeout action needed.
                if task.status not in (
                    TaskStatus.READY.value,
                    TaskStatus.PENDING.value,
                ):
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                queue_id = metadata.get("queue_id") or metadata.get("engine_id")
                message_id = metadata.get("stream_message_id")

                # If already claimed into PEL, it has been picked up.
                if (
                    queue_id
                    and message_id
                    and await self._is_stream_message_pending(queue_id, message_id)
                ):
                    await self._clear_waiting_task_marker(task_id, metadata_key)
                    continue

                wait_timeout_s = metadata.get("wait_timeout_s") or str(
                    getattr(self._settings, "engine_wait_timeout_seconds", 300)
                )
                engine_id = metadata.get("engine_id") or task.engine_id
                error = (
                    f"Engine '{engine_id}' did not become available "
                    f"within {wait_timeout_s} seconds"
                )

                # Block execution first to avoid race where an engine claims
                # the stream message before we finish timeout handling.
                await self._redis.hset(
                    metadata_key,
                    mapping={
                        "blocked_reason": "engine_wait_timeout",
                        "blocked_at": now.isoformat(),
                    },
                )
                await publish_event(
                    self._redis,
                    "task.wait_timeout",
                    {
                        "task_id": task_id,
                        "error": error,
                        "engine_id": engine_id,
                        "queue_id": queue_id,
                    },
                )

                if queue_id and message_id:
                    from dalston.common.streams import _stream_key

                    await self._redis.xdel(_stream_key(queue_id), message_id)

                await self._clear_waiting_task_marker(task_id, metadata_key)
                timed_out_count += 1
                logger.warning(
                    "task_wait_for_engine_timeout",
                    task_id=task_id,
                    engine_id=engine_id,
                    queue_id=queue_id,
                    wait_timeout_s=wait_timeout_s,
                )

        return timed_out_count

    async def _is_stream_message_pending(self, queue_id: str, message_id: str) -> bool:
        """Return True when a stream message is currently in the PEL."""
        from dalston.common.streams import _stream_key

        try:
            pending = await self._redis.xpending_range(
                _stream_key(queue_id),
                CONSUMER_GROUP,
                min=message_id,
                max=message_id,
                count=1,
            )
            return bool(pending)
        except Exception:
            return False

    async def _clear_waiting_task_marker(self, task_id: str, metadata_key: str) -> None:
        """Remove waiting-for-engine tracking markers for a task."""
        await self._redis.srem(WAITING_ENGINE_TASKS_KEY, task_id)
        await self._redis.hdel(
            metadata_key,
            "waiting_for_engine",
            "wait_deadline_at",
            "wait_timeout_s",
            "wait_enqueued_at",
        )
