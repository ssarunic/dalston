"""Stream polling runner for batch processing engines."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import signal
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dalston.common.node_identity import NodeIdentity

import redis
import structlog

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.artifacts import ArtifactReference
from dalston.common.durable_events import add_durable_event_sync
from dalston.common.engine_yaml import generate_instance_id, is_port_in_use
from dalston.common.pipeline_types import PIPELINE_SCHEMA_VERSION
from dalston.common.registry import EngineRecord, UnifiedRegistryWriter
from dalston.common.streams_sync import (
    STALE_THRESHOLD_MS,
    StreamMessage,
    ack_task,
    claim_stale_from_dead_engines,
    is_job_cancelled,
    read_own_pending,
    read_task,
)
from dalston.common.streams_types import WAITING_ENGINE_TASKS_KEY
from dalston.engine_sdk import io
from dalston.engine_sdk.admission import TaskDeferredError
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.materializer import ArtifactMaterializer, S3ArtifactStore
from dalston.engine_sdk.types import TaskRequest, TaskResponse
from dalston.orchestrator.catalog import get_catalog

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine
    from dalston.engine_sdk.vram_budget import AdaptiveVRAMParams


logger = structlog.get_logger()


class EngineRunner:
    """Queue polling runner that drives engine processing.

    The runner:
    1. Connects to Redis for queue polling and event publishing
    2. Polls the engine's stream (dalston:stream:{engine_id})
    3. Downloads task input from S3
    4. Calls engine.process()
    5. Uploads task output to S3
    6. Publishes completion/failure events
    7. Cleans up temp files
    """

    # Redis key patterns (display only - actual stream key built by streams module)
    STREAM_KEY = "dalston:stream:{engine_id}"
    EVENTS_CHANNEL = "dalston:events"

    # Configuration
    STREAM_POLL_TIMEOUT = 30  # seconds
    TEMP_DIR_PREFIX = "dalston_task_"
    HEARTBEAT_INTERVAL = 10  # seconds between heartbeats
    HEARTBEAT_TTL = 60  # auto-expire heartbeat if engine crashes
    DURABLE_EVENT_MAX_RETRIES = 5
    DURABLE_EVENT_BASE_BACKOFF_SECONDS = 0.1
    DEFAULT_TASK_TIMEOUT = 600  # seconds — fallback if no timeout_at in task

    # Temp directory purge policy: sweep orphaned dalston_task_* dirs on startup
    # and periodically.  Default max age = 30 days; override with env var (seconds).
    TEMP_PURGE_MAX_AGE_S = int(
        os.environ.get("DALSTON_TEMP_PURGE_MAX_AGE_S", str(30 * 24 * 3600))
    )
    TEMP_PURGE_INTERVAL_S = 3600  # check once per hour

    def __init__(self, engine: Engine) -> None:
        """Initialize the runner.

        Args:
            engine: Engine instance to run tasks with
        """
        self.engine = engine
        self.engine._runner = self  # Back-reference for adaptive params access
        self._adaptive_params: AdaptiveVRAMParams | None = None
        self._redis: redis.Redis | None = None
        self._unified_writer: UnifiedRegistryWriter | None = None
        self._running = False
        self._http_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._current_task_id: str | None = None
        self._current_message_id: str | None = None  # Stream message ID for ack
        self._current_stream_id: str | None = None  # Source stream for ack
        self._task_lock = threading.Lock()  # Protects _current_task_id
        self._stage: str = "unknown"  # Pipeline stage from capabilities
        self._execution_profile = "container"
        self._supports_realtime = bool(os.environ.get("DALSTON_WORKER_PORT"))
        self._node: NodeIdentity | None = None
        self._materializer = ArtifactMaterializer(store=S3ArtifactStore())
        self._tmp_root: Path = Path(tempfile.gettempdir()).resolve()

        self.engine_id = engine.engine_id
        self.instance = generate_instance_id(self.engine_id)
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.s3_bucket = os.environ.get("DALSTON_S3_BUCKET", "dalston-artifacts")
        self.metrics_port = int(os.environ.get("DALSTON_METRICS_PORT", "9100"))

        # Configure structured logging (use logical engine_id for aggregation)
        dalston.logging.configure(f"engine-{self.engine_id}")

        # Configure distributed tracing (M19)
        dalston.telemetry.configure_tracing(f"dalston-engine-{self.engine_id}")

        # Configure Prometheus metrics (M20)
        dalston.metrics.configure_metrics(f"engine-{self.engine_id}")

        logger.info(
            "engine_runner_initialized",
            engine_id=self.engine_id,
            instance=self.instance,
            pipeline_schema_version=PIPELINE_SCHEMA_VERSION,
        )

    @property
    def redis_client(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._redis is None:
            self._redis = redis.from_url(
                self.redis_url,
                decode_responses=True,
            )
        return self._redis

    @property
    def stream_key(self) -> str:
        """Get the Redis stream key for this engine."""
        return self.STREAM_KEY.format(engine_id=self.engine_id)

    def run(self) -> None:
        """Start the processing loop.

        This method blocks until the engine is stopped via SIGTERM/SIGINT.
        """
        self._running = True
        self._setup_signal_handlers()

        # Start engine HTTP server in background thread (replaces _MetricsHandler — M79)
        self._start_http_server()

        # Initialize registry and register engine with capabilities
        capabilities = self.engine.get_capabilities()
        catalog_entry = get_catalog().get_engine(self.engine_id)
        if catalog_entry is not None:
            self._execution_profile = catalog_entry.execution_profile
            if self._execution_profile != "container":
                raise RuntimeError(
                    f"Runtime '{self.engine_id}' declares execution_profile "
                    f"'{self._execution_profile}' and cannot start as a "
                    "distributed container worker."
                )

        # Get stage from capabilities (derived from engine.yaml) or fallback to "unknown"
        self._stage = capabilities.stages[0] if capabilities.stages else "unknown"

        # Batch runner always registers as batch-only. In unified engines
        # the realtime runner registers separately with ["realtime"] and its
        # own capacity — keeping them separate avoids double-counting.
        interfaces = ["batch"]
        endpoint = None

        # M78: Detect node identity and GPU total for infrastructure topology
        from dalston.common.node_identity import (
            detect_node_identity,
            get_gpu_memory_total,
        )

        self._node = detect_node_identity()
        gpu_total = get_gpu_memory_total()

        # Register with unified engine registry
        self._unified_writer = UnifiedRegistryWriter(self.redis_url)
        self._unified_writer.register(
            EngineRecord(
                instance=self.instance,
                engine_id=self.engine_id,
                stage=self._stage,
                status="idle",
                interfaces=interfaces,
                capacity=1,
                stream_name=self.stream_key,
                endpoint=endpoint,
                capabilities=capabilities,
                execution_profile=self._execution_profile,
                supports_word_timestamps=(
                    capabilities.supports_word_timestamps if capabilities else False
                ),
                includes_diarization=(
                    capabilities.includes_diarization if capabilities else False
                ),
                schema_version=PIPELINE_SCHEMA_VERSION,
                hostname=self._node.hostname,
                node_id=self._node.node_id,
                deploy_env=self._node.deploy_env,
                aws_az=self._node.region,
                aws_instance_type=self._node.instance_type,
                gpu_memory_total=gpu_total,
            )
        )
        logger.info("engine_registered", instance=self.instance)

        # Start heartbeat thread to advertise engine status
        self._start_heartbeat_thread()

        # M84: Compute VRAM budget and set adaptive params
        self._init_vram_budget()

        logger.info(
            "engine_loop_starting",
            engine_id=self.engine_id,
            queue=self.stream_key,
            execution_profile=self._execution_profile,
        )

        try:
            while self._running:
                try:
                    self._poll_and_process()
                except redis.ConnectionError as e:
                    logger.error("redis_connection_error", error=str(e))
                    time.sleep(5)  # Wait before reconnecting
                    self._redis = None  # Force reconnection
                except Exception as e:
                    logger.exception("engine_loop_error", error=str(e))
                    time.sleep(1)
        finally:
            # Cleanup - ensure resources are released even on unexpected exit
            self._stop_heartbeat_thread()
            # Call engine shutdown hook for resource cleanup (M39.2)
            try:
                self.engine.shutdown()
            except Exception as e:
                logger.warning("engine_shutdown_error", error=str(e))
            dalston.telemetry.shutdown_tracing()
            logger.info("engine_loop_stopped")

    def stop(self) -> None:
        """Stop the processing loop."""
        self._running = False
        if self._unified_writer:
            try:
                self._unified_writer.deregister(self.instance)
                self._unified_writer.close()
            except Exception:
                pass  # Best effort cleanup
            self._unified_writer = None

    # -- M84: VRAM budget integration -----------------------------------------

    def _init_vram_budget(self) -> None:
        """Compute adaptive VRAM params at startup and apply static ones.

        Reads DALSTON_VRAM_BUDGET_MB or DALSTON_VRAM_SHARE.  If neither
        is set, this is a no-op and the engine uses existing env-var
        defaults (full backward compatibility).

        If DALSTON_MODEL_PRELOAD is set, loads the calibration profile
        immediately.  Otherwise, defers profile lookup to the first task
        via ``_ensure_vram_profile(model_id)``.
        """
        from dalston.engine_sdk.vram_budget import resolve_vram_budget

        budget_mb = resolve_vram_budget()
        if budget_mb is None:
            logger.info("vram_budget_skip", reason="no budget configured")
            return

        self._vram_budget_mb = budget_mb

        # If a preload model is configured, load the profile now
        model_id = os.environ.get("DALSTON_MODEL_PRELOAD", "")
        if model_id:
            self._apply_vram_profile(model_id, budget_mb)
        else:
            logger.info(
                "vram_budget_deferred",
                budget_mb=budget_mb,
                reason="no DALSTON_MODEL_PRELOAD, profile will load on first task",
            )

    def _ensure_vram_profile(self, model_id: str) -> None:
        """Load calibration profile on first task if not already loaded.

        Called by the engine loop when processing the first task that
        specifies a model_id.
        """
        if self._adaptive_params is not None:
            return
        budget_mb = getattr(self, "_vram_budget_mb", None)
        if budget_mb is None or not model_id:
            return
        self._apply_vram_profile(model_id, budget_mb)

    def _apply_vram_profile(self, model_id: str, budget_mb: int) -> None:
        """Load a calibration profile and apply adaptive params."""
        from dalston.engine_sdk.vram_budget import VRAMBudget

        vram = VRAMBudget.load(self.engine_id, model_id)
        adaptive = vram.compute_adaptive_params(budget_mb)

        self._set_env_if_absent(
            "DALSTON_BATCH_MAX_INFLIGHT",
            str(adaptive.concurrent.batch_max_inflight),
        )
        self._set_env_if_absent(
            "DALSTON_MAX_SESSIONS", str(adaptive.concurrent.max_sessions)
        )
        self._set_env_if_absent(
            "DALSTON_MAX_DIARIZE_CHUNK_S",
            str(adaptive.solo.max_diarize_chunk_s),
        )

        self._adaptive_params = adaptive

        # Wire OOM callback so inference can cache safe batch sizes
        core = getattr(self.engine, "_core", None)
        if core and hasattr(core, "_oom_callback"):
            core._oom_callback = lambda safe: adaptive.update_safe_batch_size(safe)

        logger.info(
            "vram_budget_computed",
            budget_mb=budget_mb,
            model_id=model_id,
            solo={
                "vad_batch": adaptive.solo.vad_batch_size,
                "inflight": adaptive.solo.batch_max_inflight,
                "peak_mb": adaptive.solo.peak_estimate_mb,
            },
            concurrent={
                "vad_batch": adaptive.concurrent.vad_batch_size,
                "inflight": adaptive.concurrent.batch_max_inflight,
                "peak_mb": adaptive.concurrent.peak_estimate_mb,
            },
            profile_source=adaptive.profile_source,
        )

    @staticmethod
    def _set_env_if_absent(key: str, value: str) -> None:
        """Set an environment variable only if not already set."""
        if key not in os.environ:
            os.environ[key] = value
            logger.debug("vram_budget_set_env", key=key, value=value)

    def get_queue_depth(self) -> int:
        """Get the number of pending messages in this engine's Redis stream.

        O(1) operation via XLEN.
        """
        try:
            return self.redis_client.xlen(self.stream_key)
        except Exception:
            return 0

    def get_adaptive_params(self) -> AdaptiveVRAMParams | None:
        """Return the adaptive VRAM params, or None if not configured."""
        return self._adaptive_params

    def _start_http_server(self) -> None:
        """Start the engine HTTP server in a background thread.

        Replaces the old ``_MetricsHandler`` with a FastAPI-based server
        that serves ``/health``, ``/metrics``, ``/v1/capabilities``, and
        stage-specific POST endpoints.  Same port (9100 default), same
        paths — existing healthchecks and Prometheus scrape configs are
        unaffected.

        In unified engines the realtime runner already binds this port
        (aiohttp with ``/health``, ``/metrics``, ``/v1/capabilities``),
        so we probe first and skip if the port is occupied.
        """
        try:
            if is_port_in_use(self.metrics_port):
                logger.info(
                    "http_server_skipped_port_in_use",
                    port=self.metrics_port,
                )
                return

            http_server = self.engine.create_http_server(port=self.metrics_port)
            self._http_thread = threading.Thread(
                target=lambda: asyncio.run(http_server.serve()),
                daemon=True,
            )
            self._http_thread.start()
            logger.info("http_server_started", port=self.metrics_port)
        except Exception as e:
            logger.warning("http_server_failed", error=str(e))

    def _start_heartbeat_thread(self) -> None:
        """Start heartbeat thread to advertise engine status."""
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info("heartbeat_thread_started")

    def _stop_heartbeat_thread(self) -> None:
        """Stop heartbeat thread."""
        if self._heartbeat_thread:
            # Thread will exit when _running becomes False
            self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        """Send heartbeats to Redis periodically via registry.

        M36: Also reports the currently loaded model from engine's engine_id state.
        This allows the orchestrator to know which model is loaded without
        requiring a model swap.
        """
        last_purge = time.monotonic()
        # Run an initial sweep on startup to catch leftovers from crashes
        self._purge_stale_temp_dirs()

        while self._running:
            try:
                # Read current task with lock for thread safety
                with self._task_lock:
                    current_task = self._current_task_id

                # M36: Get engine_id state including loaded model and engine status
                runtime_state = self.engine.get_runtime_state()
                loaded_model = runtime_state.get("loaded_model")
                engine_status = runtime_state.get("status", "idle")

                # Use "processing"/"busy" when actively working on a task,
                # otherwise use the engine's reported status.
                # Unified engines use ready/busy vocabulary so the session
                # coordinator treats them identically to pure-RT workers.
                if current_task:
                    status = "busy" if self._supports_realtime else "processing"
                elif engine_status == "idle" and self._supports_realtime:
                    status = "ready"
                else:
                    status = engine_status

                if self._unified_writer:
                    try:
                        from dalston.common.node_identity import get_gpu_memory_used

                        self._unified_writer.heartbeat(
                            self.instance,
                            status=status,
                            active_batch=1 if current_task else 0,
                            loaded_model=loaded_model,
                            engine_id=self.engine_id,
                            stage=self._stage,
                            hostname=self._node.hostname,
                            node_id=self._node.node_id,
                            deploy_env=self._node.deploy_env,
                            gpu_memory_used=get_gpu_memory_used(),
                        )
                    except Exception as e:
                        logger.warning("unified_heartbeat_failed", error=str(e))

                # Periodic temp dir purge
                now = time.monotonic()
                if now - last_purge >= self.TEMP_PURGE_INTERVAL_S:
                    self._purge_stale_temp_dirs()
                    last_purge = now

            except Exception as e:
                logger.warning("heartbeat_failed", error=str(e))
            time.sleep(self.HEARTBEAT_INTERVAL)

    # Directories that are safe to sweep.  If tempfile.gettempdir() resolves
    # to anything outside this set (e.g. "/" due to a misconfigured TMPDIR)
    # the sweeper refuses to run.
    _SAFE_TMP_ROOTS = frozenset({"/tmp", "/var/tmp", "/private/tmp"})

    def _purge_stale_temp_dirs(self) -> None:
        """Remove dalston_task_* directories older than TEMP_PURGE_MAX_AGE_S.

        Defence-in-depth for orphans left by OOM-killed tasks inside a
        still-running container.  The primary crash-recovery mechanism is
        the tmpfs mount on /tmp (wiped automatically on container restart).

        Guardrails:
        - Only runs if gettempdir() resolves to a known safe root.
        - Only targets directories matching TEMP_DIR_PREFIX exactly.
        - Skips the directory used by the current active task.
        - Caps the scan to 1 000 entries to avoid stalling on large dirs.
        """
        tmp_root = self._tmp_root

        # Refuse to scan anything that doesn't look like a temp directory
        if str(tmp_root) not in self._SAFE_TMP_ROOTS:
            logger.warning(
                "temp_purge_skipped_unsafe_root",
                tmp_root=str(tmp_root),
                safe_roots=sorted(self._SAFE_TMP_ROOTS),
            )
            return

        cutoff = time.time() - self.TEMP_PURGE_MAX_AGE_S
        removed = 0
        scanned = 0
        max_scan = 1000

        try:
            for entry in tmp_root.iterdir():
                scanned += 1
                if scanned > max_scan:
                    break
                if not entry.name.startswith(self.TEMP_DIR_PREFIX):
                    continue
                if not entry.is_dir():
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime >= cutoff:
                    continue
                # Don't remove the dir being used by the current task
                with self._task_lock:
                    current = self._current_task_id
                if current and current in entry.name:
                    continue
                try:
                    shutil.rmtree(entry)
                    removed += 1
                except OSError:
                    pass
        except OSError:
            # /tmp itself unreadable — nothing to do
            pass

        if removed:
            logger.info("stale_temp_dirs_purged", count=removed)

    def _setup_signal_handlers(self) -> None:
        """Setup handlers for graceful shutdown.

        Signal handlers can only be installed from the main thread.
        When the runner is started in a background thread (e.g. by the
        unified runner), signal handling is the caller's responsibility.
        """
        if threading.current_thread() is not threading.main_thread():
            logger.debug("skip_signal_handlers", reason="not_main_thread")
            return

        def handle_signal(signum, frame):
            logger.info("shutdown_signal_received", signal=signum)
            self.stop()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    def _poll_and_process(self) -> None:
        """Poll the stream and process one task.

        Uses Redis Streams with consumer groups for crash-resilient delivery:
        1. Try to claim stale tasks from dead engines first (recovery)
        2. If no stale tasks, read new ones via XREADGROUP
        3. Always ACK after processing (success or failure)
        """
        message: StreamMessage | None = None
        stream_id = self.engine_id

        # 1. Try to claim stale tasks from DEAD engines only
        # Use instance_id as consumer to ensure spot replacements don't mask old tasks
        stale = claim_stale_from_dead_engines(
            self.redis_client,
            stage=stream_id,
            consumer=self.instance,
            min_idle_ms=STALE_THRESHOLD_MS,
            count=1,
        )
        if stale:
            message = stale[0]
            logger.info(
                "claimed_stale_task",
                task_id=message.task_id,
                delivery_count=message.delivery_count,
                previous_consumer=message.id,  # Actually message ID, consumer tracked in PEL
                stream_id=stream_id,
            )
            # Track redelivery for observability
            dalston.metrics.inc_task_redelivery(self._stage, reason="engine_crash")

        if message is None:
            # 1b. Check for our own deferred (unACKed) messages.
            # When admission control rejects a task, we skip the ACK so it
            # stays in the PEL.  Re-read it here before fetching new work.
            message = read_own_pending(
                self.redis_client,
                stage=stream_id,
                consumer=self.instance,
            )
            if message is not None:
                logger.info(
                    "reclaimed_deferred_task",
                    task_id=message.task_id,
                    stream_id=stream_id,
                )
                dalston.metrics.inc_task_redelivery(self._stage, reason="deferred")

        if message is None:
            # 2. No stale or deferred tasks - read new ones
            # Use instance_id as consumer for proper PEL tracking
            message = read_task(
                self.redis_client,
                stage=stream_id,
                consumer=self.instance,
                block_ms=self.STREAM_POLL_TIMEOUT * 1000,
            )

        if message is None:
            # Timeout, no task available
            return

        logger.info(
            "task_received",
            task_id=message.task_id,
            job_id=message.job_id,
            delivery_count=message.delivery_count,
            is_redelivery=message.delivery_count > 1,
            stream_id=stream_id,
        )

        # Store message ID for ack in finally block
        self._current_message_id = message.id
        self._current_stream_id = stream_id

        # 3. Check if job is cancelled before processing
        if is_job_cancelled(self.redis_client, message.job_id):
            self._clear_waiting_engine_marker(message.task_id)
            logger.info(
                "task_skipped_job_cancelled",
                task_id=message.task_id,
                job_id=message.job_id,
            )
            dalston.metrics.inc_tasks_skipped_cancelled(self._stage)
            # ACK the task so it's removed from PEL
            ack_task(
                self.redis_client,
                self._current_stream_id or self.engine_id,
                self._current_message_id,
            )
            self._current_message_id = None
            self._current_stream_id = None
            return

        try:
            task_metadata = self._get_task_metadata(message.task_id)
        except Exception as e:
            logger.exception(
                "task_metadata_lookup_failed_before_process",
                task_id=message.task_id,
                error=str(e),
            )
            self._publish_task_failed(message.task_id, message.job_id, str(e))
            ack_task(
                self.redis_client,
                self._current_stream_id or self.engine_id,
                self._current_message_id,
            )
            self._current_message_id = None
            self._current_stream_id = None
            return

        blocked_reason = task_metadata.get("blocked_reason")
        if blocked_reason:
            logger.info(
                "task_skipped_blocked",
                task_id=message.task_id,
                blocked_reason=blocked_reason,
            )
            ack_task(
                self.redis_client,
                self._current_stream_id or self.engine_id,
                self._current_message_id,
            )
            self._current_message_id = None
            self._current_stream_id = None
            return

        # Task is now claimed by an engine instance; clear wait markers.
        self._clear_waiting_engine_marker(message.task_id)

        try:
            self._process_task(message.task_id, task_metadata)
        except TaskDeferredError:
            # Task was deferred (e.g. admission control rejection).
            # Skip ACK so the message stays in the PEL for redelivery.
            logger.info(
                "task_deferred_skipping_ack",
                task_id=message.task_id,
                stream_id=self._current_stream_id or self.engine_id,
            )
            self._current_message_id = None
            self._current_stream_id = None
        else:
            # 4. ACK on success or failure — failure handling is via task.failed event
            if self._current_message_id:
                ack_task(
                    self.redis_client,
                    self._current_stream_id or self.engine_id,
                    self._current_message_id,
                )
                self._current_message_id = None
                self._current_stream_id = None

    def _clear_waiting_engine_marker(self, task_id: str) -> None:
        """Clear wait-for-engine markers once a task is claimed."""
        key = f"dalston:task:{task_id}"
        try:
            self.redis_client.hdel(
                key,
                "waiting_for_engine",
                "wait_deadline_at",
                "wait_timeout_s",
                "wait_enqueued_at",
            )
            self.redis_client.srem(WAITING_ENGINE_TASKS_KEY, task_id)
        except Exception:
            logger.debug("clear_waiting_engine_marker_failed", task_id=task_id)

    def _get_task_timeout(self, task_metadata: dict[str, Any]) -> float:
        """Get remaining seconds until task timeout from metadata."""
        timeout_str = task_metadata.get("timeout_at", "")
        if timeout_str:
            try:
                timeout_at = datetime.fromisoformat(timeout_str)
                remaining = (timeout_at - datetime.now(UTC)).total_seconds()
                return max(remaining, 10.0)  # At least 10s to avoid instant timeout
            except (ValueError, TypeError):
                pass
        return float(self.DEFAULT_TASK_TIMEOUT)

    def _process_task(
        self,
        task_id: str,
        task_metadata: dict[str, Any],
    ) -> None:
        """Process a single task.

        Args:
            task_id: ID of the task to process
            task_metadata: Pre-fetched task metadata from Redis
        """
        temp_dir = None
        start_time = time.time()

        # Track current task for heartbeat status (thread-safe)
        with self._task_lock:
            self._current_task_id = task_id

        # Extract model from task config (set by orchestrator's engine selector)
        task_model = task_metadata.get("loaded_model_id", "")

        # Record queue wait time (M20) - time between enqueue and dequeue
        enqueued_at_str = task_metadata.get("enqueued_at")
        if enqueued_at_str:
            try:
                enqueued_at = datetime.fromisoformat(enqueued_at_str)
                dequeued_at = datetime.now(UTC)
                queue_wait_seconds = (dequeued_at - enqueued_at).total_seconds()
                dalston.metrics.observe_engine_queue_wait(
                    self.engine_id,
                    queue_wait_seconds,
                    self._execution_profile,
                )
            except ValueError:
                pass  # Skip if timestamp is malformed

        trace_context_raw = task_metadata.get("_trace_context")
        try:
            trace_context = json.loads(trace_context_raw) if trace_context_raw else {}
        except json.JSONDecodeError:
            trace_context = {}

        # Create span linked to parent trace from orchestrator
        with dalston.telemetry.span_from_context(
            f"engine.{self.engine_id}.process",
            trace_context,
            attributes={
                "dalston.task_id": task_id,
                "dalston.engine_id": self.engine_id,
                "dalston.model": task_model,
                "dalston.stage": task_metadata.get("stage", "unknown"),
                "dalston.instance": self.instance,
            },
        ):
            try:
                # Create temp directory for this task
                temp_dir = Path(tempfile.mkdtemp(prefix=self.TEMP_DIR_PREFIX))

                # Load task request from S3
                download_start = time.time()
                with dalston.telemetry.create_span("engine.download_input"):
                    task_request = self._load_task_request(task_id, temp_dir)
                dalston.metrics.observe_engine_s3_download(
                    self.engine_id,
                    time.time() - download_start,
                    self._execution_profile,
                )
                job_id = task_request.job_id

                # Resolve model from task request config if not in metadata
                if not task_model:
                    task_model = task_request.config.get("loaded_model_id", "")
                    if task_model:
                        dalston.telemetry.set_span_attribute(
                            "dalston.model", task_model
                        )

                # Deferred VRAM profile: load on first task with a model_id
                if task_model:
                    self._ensure_vram_profile(task_model)

                # Set job_id on span
                dalston.telemetry.set_span_attribute("dalston.job_id", job_id)

                # Extract request_id from task metadata and bind to logger
                request_id = task_metadata.get("request_id")
                structlog.contextvars.bind_contextvars(
                    task_id=task_id,
                    job_id=job_id,
                    engine_id=self.engine_id,
                    model=task_model,
                    **({"request_id": request_id} if request_id else {}),
                )

                # Notify orchestrator that processing has started
                self._publish_task_started(task_id, job_id)

                # Process the task with timeout guard
                task_timeout = self._get_task_timeout(task_metadata)
                logger.info("task_processing", timeout_s=round(task_timeout, 1))
                process_start = time.time()
                task_ctx = BatchTaskContext(
                    engine_id=self.engine_id,
                    instance=self.instance,
                    task_id=task_id,
                    job_id=job_id,
                    stage=task_request.stage,
                    metadata=task_metadata,
                    logger=self.engine.logger,
                    temp_dir=temp_dir,
                )
                # M81: Ensure audio matches engine's declared format
                if self.engine.audio_format is not None and isinstance(
                    task_request.audio_path, Path
                ):
                    from dalston.engine_sdk.audio import ensure_audio_format

                    task_request.audio_path = ensure_audio_format(
                        task_request.audio_path,
                        target=self.engine.audio_format,
                        work_dir=temp_dir,
                    )

                with dalston.telemetry.create_span("engine.process"):
                    # M76: Propagate OTel context into the worker thread
                    # so that sub-spans (model_acquire, recognize, etc.)
                    # appear as children rather than orphan traces.
                    from opentelemetry import context as otel_context

                    parent_ctx = otel_context.get_current()

                    def _run_with_context():
                        token = otel_context.attach(parent_ctx)
                        try:
                            return self.engine.process(task_request, task_ctx)
                        finally:
                            otel_context.detach(token)

                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=1
                    ) as executor:
                        future = executor.submit(_run_with_context)
                        try:
                            output = future.result(timeout=task_timeout)
                        except concurrent.futures.TimeoutError as exc:
                            raise TimeoutError(
                                f"Task processing exceeded {task_timeout:.0f}s timeout"
                            ) from exc
                process_time = time.time() - process_start

                # Boundary validation: validate transcribe outputs against
                # Transcript. Covers both mono ("transcribe") and per-channel
                # ("transcribe_ch0", "transcribe_ch1", ...) stages.
                if task_request.stage.startswith("transcribe"):
                    self._validate_transcript_output(output, task_id)

                # Calculate total task time (for metrics)
                total_task_time = time.time() - start_time

                # Upload output to S3
                upload_start = time.time()
                with dalston.telemetry.create_span("engine.upload_output"):
                    self._save_task_output(
                        task_id, job_id, output, total_task_time, task_request.stage
                    )
                dalston.metrics.observe_engine_s3_upload(
                    self.engine_id,
                    time.time() - upload_start,
                    self._execution_profile,
                )

                # Record task success metrics (M20)
                dalston.metrics.observe_engine_task_duration(
                    self.engine_id,
                    task_model,
                    process_time,
                    self._execution_profile,
                )
                dalston.metrics.inc_engine_tasks(
                    self.engine_id,
                    task_model,
                    "success",
                    self._execution_profile,
                )

                # Publish success event
                self._publish_task_completed(task_id, job_id)

                logger.info("task_completed", processing_time=round(total_task_time, 2))

            except TaskDeferredError:
                # Task was deferred (e.g. admission control rejection).
                # Re-raise so _poll_and_process skips the ACK, leaving the
                # message in the PEL for later redelivery.
                logger.info("task_deferred", task_id=task_id)
                raise

            except Exception as e:
                dalston.telemetry.record_exception(e)
                dalston.telemetry.set_span_status_error(str(e))
                logger.exception("task_failed", error=str(e))

                # Record task failure metric (M20)
                dalston.metrics.inc_engine_tasks(
                    self.engine_id,
                    task_model,
                    "failure",
                    self._execution_profile,
                )

                # We need job_id for the event, try to extract from request
                try:
                    job_id = task_request.job_id
                except NameError:
                    job_id = "unknown"
                self._publish_task_failed(task_id, job_id, str(e))

            finally:
                # Clear current task tracking for heartbeat (thread-safe)
                with self._task_lock:
                    self._current_task_id = None
                # Cleanup temp directory
                if temp_dir and temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.debug("temp_dir_cleaned", path=str(temp_dir))
                # Clear per-task context
                structlog.contextvars.unbind_contextvars(
                    "task_id",
                    "job_id",
                    "request_id",
                )

    def _load_task_request(self, task_id: str, temp_dir: Path) -> TaskRequest:
        """Load task request from S3 and materialize required artifacts."""
        task_metadata = self._get_task_metadata(task_id)
        job_id = task_metadata["job_id"]
        stage = task_metadata.get("stage", "unknown")

        request_uri = io.build_task_request_uri(self.s3_bucket, job_id, task_id)
        request_data = io.download_json(request_uri)

        # Canonical M51 fields.
        payload = request_data.get("payload")
        resolved_artifact_ids = request_data.get("resolved_artifact_ids", {})
        artifact_index_data = request_data.get("artifact_index", {})

        # Read from task metadata when scheduler stores JSON there.
        resolved_from_metadata = task_metadata.get("resolved_artifact_ids_json")
        if resolved_from_metadata:
            try:
                resolved_artifact_ids = json.loads(resolved_from_metadata)
            except json.JSONDecodeError:
                logger.warning("invalid_resolved_artifact_ids_json", task_id=task_id)

        artifact_index: dict[str, ArtifactReference] = {}
        for artifact_id, metadata in artifact_index_data.items():
            artifact_index[artifact_id] = ArtifactReference.model_validate(metadata)

        materialized_artifacts = self._materializer.materialize(
            resolved_artifact_ids=resolved_artifact_ids,
            artifact_index=artifact_index,
            target_dir=temp_dir / "materialized",
        )

        return TaskRequest(
            task_id=task_id,
            job_id=job_id,
            stage=stage,
            config=request_data.get("config", {}),
            payload=payload,
            previous_responses=request_data.get("previous_responses", {}),
            materialized_artifacts=materialized_artifacts,
            metadata={"resolved_artifact_ids": resolved_artifact_ids},
        )

    def _get_task_metadata(self, task_id: str) -> dict[str, Any]:
        """Get task metadata from Redis.

        The orchestrator stores minimal task info in Redis when queueing.

        Args:
            task_id: Task identifier

        Returns:
            Dictionary with task metadata (at minimum: job_id)
        """
        key = f"dalston:task:{task_id}"
        data = self.redis_client.hgetall(key)

        if not data:
            raise ValueError(f"Task metadata not found in Redis: {task_id}")

        return data

    def _validate_transcript_output(
        self,
        output: TaskResponse,
        task_id: str,
    ) -> None:
        """Validate transcribe-stage output against Transcript.

        Raises ValueError if the output does not conform to the Transcript
        schema. All transcribe engines must return valid Transcript output.
        """
        from dalston.common.pipeline_types import Transcript

        output_dict = output.to_dict()
        try:
            Transcript.model_validate(output_dict)
        except Exception as e:
            raise ValueError(
                f"Transcribe output failed Transcript validation: {e}"
            ) from e

    def _save_task_output(
        self,
        task_id: str,
        job_id: str,
        output: TaskResponse,
        processing_time: float,
        stage: str,
    ) -> None:
        """Save task response to S3.

        Args:
            task_id: Task identifier
            job_id: Job identifier
            output: Task output from engine
            processing_time: Time taken to process in seconds
            stage: Pipeline stage name
        """
        response_uri = io.build_task_response_uri(self.s3_bucket, job_id, task_id)

        response_data = {
            "task_id": task_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "processing_time_seconds": round(processing_time, 2),
            "data": output.to_dict(),
        }

        task_stage = stage

        persisted_artifacts = self._materializer.persist_produced(
            job_id=job_id,
            task_id=task_id,
            stage=task_stage,
            produced_artifacts=output.produced_artifacts,
        )
        response_data["produced_artifacts"] = [
            artifact.model_dump(mode="json", exclude_none=True)
            for artifact in persisted_artifacts
        ]
        response_data["produced_artifact_ids"] = [
            artifact.artifact_id for artifact in persisted_artifacts
        ]
        canonical_transcript = next(
            (
                artifact
                for artifact in persisted_artifacts
                if artifact.kind == "transcript" and artifact.role == "final"
            ),
            None,
        )
        if canonical_transcript is not None:
            response_data["canonical_transcript_uri"] = (
                canonical_transcript.storage_locator
            )

        io.upload_json(response_data, response_uri)
        logger.info("response_uploaded", response_uri=response_uri)

        if persisted_artifacts:
            metadata_key = f"dalston:task:{task_id}"
            produced_ids = [artifact.artifact_id for artifact in persisted_artifacts]
            self.redis_client.hset(
                metadata_key,
                mapping={"produced_artifact_ids_json": json.dumps(produced_ids)},
            )

            artifact_key = f"dalston:job:{job_id}:artifacts"
            for artifact in persisted_artifacts:
                self.redis_client.hset(
                    artifact_key,
                    artifact.artifact_id,
                    artifact.model_dump_json(exclude_none=True),
                )

    def _publish_durable_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        task_id: str,
    ) -> bool:
        """Write durable event with retry/backoff."""
        for attempt in range(self.DURABLE_EVENT_MAX_RETRIES):
            try:
                add_durable_event_sync(self.redis_client, event_type, payload)
                return True
            except Exception as e:
                if attempt < self.DURABLE_EVENT_MAX_RETRIES - 1:
                    backoff_seconds = self.DURABLE_EVENT_BASE_BACKOFF_SECONDS * (
                        2**attempt
                    )
                    logger.warning(
                        "durable_event_write_retry",
                        event_type=event_type,
                        task_id=task_id,
                        attempt=attempt + 1,
                        backoff_seconds=backoff_seconds,
                        error=str(e),
                    )
                    time.sleep(backoff_seconds)
                else:
                    logger.error(
                        "durable_event_write_failed",
                        event_type=event_type,
                        task_id=task_id,
                        error=str(e),
                    )
        return False

    def _publish_task_started(self, task_id: str, job_id: str) -> None:
        """Publish task.started event to Redis pub/sub and durable stream.

        Writes to both pub/sub (real-time delivery) and the durable events
        stream (crash recovery). This ensures the orchestrator receives the
        event even if it restarts.

        Args:
            task_id: Task identifier
            job_id: Job identifier
        """
        event = {
            "type": "task.started",
            "task_id": task_id,
            "job_id": job_id,
            "engine_id": self.engine_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # Inject trace context for distributed tracing (M19)
        trace_context = dalston.telemetry.inject_trace_context()
        if trace_context:
            event["_trace_context"] = trace_context

        durable_success = self._publish_durable_event(
            event_type="task.started",
            payload={"task_id": task_id, "job_id": job_id, "engine_id": self.engine_id},
            task_id=task_id,
        )

        # Publish to pub/sub for real-time delivery (secondary, for other consumers)
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))

        if not durable_success:
            # Log critical error - reconciliation sweeper will detect orphaned tasks
            # For task.started, the reconciler will mark as FAILED if no output exists
            logger.error(
                "critical_event_may_be_lost",
                event_type="task.started",
                task_id=task_id,
                note="reconciliation_sweeper_will_detect",
            )

        logger.debug("published_task_started")

    def _publish_task_completed(self, task_id: str, job_id: str) -> None:
        """Publish task.completed event to Redis pub/sub and durable stream.

        Writes to both pub/sub (real-time delivery) and the durable events
        stream (crash recovery). This ensures the orchestrator receives the
        event even if it restarts.

        Args:
            task_id: Task identifier
            job_id: Job identifier
        """
        event = {
            "type": "task.completed",
            "task_id": task_id,
            "job_id": job_id,
            "engine_id": self.engine_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # Inject trace context for distributed tracing (M19)
        trace_context = dalston.telemetry.inject_trace_context()
        if trace_context:
            event["_trace_context"] = trace_context

        durable_success = self._publish_durable_event(
            event_type="task.completed",
            payload={"task_id": task_id, "job_id": job_id, "engine_id": self.engine_id},
            task_id=task_id,
        )

        # Publish to pub/sub for real-time delivery (secondary, for other consumers)
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))

        if not durable_success:
            # Log critical error - reconciliation sweeper will detect orphaned tasks
            # For task.completed, reconciler checks response_uri and recovers as COMPLETED
            logger.error(
                "critical_event_may_be_lost",
                event_type="task.completed",
                task_id=task_id,
                note="reconciliation_sweeper_will_recover_via_response_uri",
            )

        logger.debug("published_task_completed")

    def _publish_task_failed(
        self,
        task_id: str,
        job_id: str,
        error: str,
    ) -> None:
        """Publish task.failed event to Redis pub/sub and durable stream.

        Writes to both pub/sub (real-time delivery) and the durable events
        stream (crash recovery). This ensures the orchestrator receives the
        event even if it restarts.

        Args:
            task_id: Task identifier
            job_id: Job identifier
            error: Error message
        """
        event = {
            "type": "task.failed",
            "task_id": task_id,
            "job_id": job_id,
            "engine_id": self.engine_id,
            "error": error,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # Inject trace context for distributed tracing (M19)
        trace_context = dalston.telemetry.inject_trace_context()
        if trace_context:
            event["_trace_context"] = trace_context

        durable_success = self._publish_durable_event(
            event_type="task.failed",
            payload={
                "task_id": task_id,
                "job_id": job_id,
                "engine_id": self.engine_id,
                "error": error,
            },
            task_id=task_id,
        )

        # Publish to pub/sub for real-time delivery (secondary, for other consumers)
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))

        if not durable_success:
            # Log critical error - reconciliation sweeper will detect orphaned tasks
            # For task.failed, reconciler marks as FAILED (no response_uri to recover)
            logger.error(
                "critical_event_may_be_lost",
                event_type="task.failed",
                task_id=task_id,
                note="reconciliation_sweeper_will_mark_failed",
            )

        logger.debug("published_task_failed")
