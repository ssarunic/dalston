"""Stream polling runner for batch processing engines."""

from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import redis
import structlog

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.durable_events import add_durable_event_sync
from dalston.common.streams_sync import (
    STALE_THRESHOLD_MS,
    StreamMessage,
    ack_task,
    claim_stale_from_dead_engines,
    is_job_cancelled,
    read_task,
)
from dalston.common.streams_types import WAITING_ENGINE_TASKS_KEY
from dalston.engine_sdk import io
from dalston.engine_sdk.registry import BatchEngineInfo, BatchEngineRegistry
from dalston.engine_sdk.types import TaskInput, TaskOutput

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


logger = structlog.get_logger()


class _MetricsHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for /metrics endpoint."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default logging."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/metrics":
            try:
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                content = generate_latest()
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        else:
            self.send_response(404)
            self.end_headers()


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

    def __init__(self, engine: Engine) -> None:
        """Initialize the runner.

        Args:
            engine: Engine instance to run tasks with
        """
        self.engine = engine
        self._redis: redis.Redis | None = None
        self._registry: BatchEngineRegistry | None = None
        self._running = False
        self._metrics_server: HTTPServer | None = None
        self._metrics_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._current_task_id: str | None = None
        self._current_message_id: str | None = None  # Stream message ID for ack
        self._current_stream_id: str | None = None  # Source stream for ack
        self._task_lock = threading.Lock()  # Protects _current_task_id
        self._stage: str = "unknown"  # Pipeline stage from capabilities

        # Load configuration from environment
        self.engine_id = os.environ.get("ENGINE_ID", "unknown")
        # Instance-unique consumer ID for Redis Streams
        # This ensures spot instance replacements don't mask old pending tasks
        self.instance_id = f"{self.engine_id}-{uuid4().hex[:12]}"
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        self.metrics_port = int(os.environ.get("METRICS_PORT", "9100"))

        # Configure structured logging (use logical engine_id for aggregation)
        dalston.logging.configure(f"engine-{self.engine_id}")

        # Configure distributed tracing (M19)
        dalston.telemetry.configure_tracing(f"dalston-engine-{self.engine_id}")

        # Configure Prometheus metrics (M20)
        dalston.metrics.configure_metrics(f"engine-{self.engine_id}")

        logger.info(
            "engine_runner_initialized",
            engine_id=self.engine_id,
            instance_id=self.instance_id,
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

        # Start metrics HTTP server in background thread (M20)
        self._start_metrics_server()

        # Initialize registry and register engine with capabilities
        self._registry = BatchEngineRegistry(self.redis_url)
        capabilities = self.engine.get_capabilities()

        # Get stage from capabilities (derived from engine.yaml) or fallback to "unknown"
        self._stage = capabilities.stages[0] if capabilities.stages else "unknown"

        self._registry.register(
            BatchEngineInfo(
                engine_id=self.engine_id,
                instance_id=self.instance_id,
                stage=self._stage,
                stream_name=self.stream_key,
                capabilities=capabilities,
            )
        )

        # Start heartbeat thread to advertise engine status
        self._start_heartbeat_thread()

        logger.info(
            "engine_loop_starting", engine_id=self.engine_id, queue=self.stream_key
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
            self._stop_metrics_server()
            dalston.telemetry.shutdown_tracing()
            logger.info("engine_loop_stopped")

    def stop(self) -> None:
        """Stop the processing loop."""
        self._running = False
        # Unregister from registry for immediate offline status
        if self._registry:
            try:
                self._registry.unregister(self.instance_id)
            except Exception:
                pass  # Best effort cleanup

    def _start_metrics_server(self) -> None:
        """Start metrics HTTP server in a background thread."""
        if not dalston.metrics.is_metrics_enabled():
            logger.debug("metrics_disabled_skipping_server")
            return

        try:
            self._metrics_server = HTTPServer(
                ("0.0.0.0", self.metrics_port), _MetricsHandler
            )
            self._metrics_thread = threading.Thread(
                target=self._metrics_server.serve_forever,
                daemon=True,
            )
            self._metrics_thread.start()
            logger.info("metrics_server_started", port=self.metrics_port)
        except Exception as e:
            logger.warning("metrics_server_failed", error=str(e))

    def _stop_metrics_server(self) -> None:
        """Stop the metrics HTTP server."""
        if self._metrics_server:
            self._metrics_server.shutdown()
            self._metrics_server = None
            logger.debug("metrics_server_stopped")

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
        """Send heartbeats to Redis periodically via registry."""
        while self._running:
            try:
                # Read current task with lock for thread safety
                with self._task_lock:
                    current_task = self._current_task_id

                if self._registry:
                    self._registry.heartbeat(
                        instance_id=self.instance_id,
                        status="processing" if current_task else "idle",
                        current_task=current_task,
                    )
            except Exception as e:
                logger.warning("heartbeat_failed", error=str(e))
            time.sleep(self.HEARTBEAT_INTERVAL)

    def _setup_signal_handlers(self) -> None:
        """Setup handlers for graceful shutdown."""

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
        stream_id: str | None = None

        # Primary stream is engine-specific; fallback to legacy stage stream for
        # mixed-version rollouts where producers/consumers might briefly diverge.
        stream_ids = self._candidate_stream_ids()

        # 1. Try to claim stale tasks from DEAD engines only
        # Use instance_id as consumer to ensure spot replacements don't mask old tasks
        for candidate_stream_id in stream_ids:
            stale = claim_stale_from_dead_engines(
                self.redis_client,
                stage=candidate_stream_id,
                consumer=self.instance_id,
                min_idle_ms=STALE_THRESHOLD_MS,
                count=1,
            )
            if stale:
                message = stale[0]
                stream_id = candidate_stream_id
                logger.info(
                    "claimed_stale_task",
                    task_id=message.task_id,
                    delivery_count=message.delivery_count,
                    previous_consumer=message.id,  # Actually message ID, consumer tracked in PEL
                    stream_id=stream_id,
                )
                # Track redelivery for observability
                dalston.metrics.inc_task_redelivery(self._stage, reason="engine_crash")
                break

        if message is None:
            # 2. No stale tasks - read new ones
            # Use instance_id as consumer for proper PEL tracking
            # Block only on primary stream to preserve previous latency profile.
            primary_stream_id = stream_ids[0]
            message = read_task(
                self.redis_client,
                stage=primary_stream_id,
                consumer=self.instance_id,
                block_ms=self.STREAM_POLL_TIMEOUT * 1000,
            )
            if message is not None:
                stream_id = primary_stream_id
            elif len(stream_ids) > 1:
                # Non-blocking legacy fallback check.
                fallback_stream_id = stream_ids[1]
                message = read_task(
                    self.redis_client,
                    stage=fallback_stream_id,
                    consumer=self.instance_id,
                    block_ms=1,
                )
                if message is not None:
                    stream_id = fallback_stream_id

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
        self._current_stream_id = stream_id or self.engine_id

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
            self._process_task(message.task_id)
        finally:
            # 4. Always ACK - failure handling is via task.failed event
            if self._current_message_id:
                ack_task(
                    self.redis_client,
                    self._current_stream_id or self.engine_id,
                    self._current_message_id,
                )
                self._current_message_id = None
                self._current_stream_id = None

    def _candidate_stream_ids(self) -> list[str]:
        """Return stream IDs to poll (engine stream first, legacy stage fallback)."""
        stream_ids = [self.engine_id]
        if self._stage and self._stage != "unknown" and self._stage != self.engine_id:
            stream_ids.append(self._stage)
        return stream_ids

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

    def _process_task(self, task_id: str) -> None:
        """Process a single task.

        Args:
            task_id: ID of the task to process
        """
        temp_dir = None
        start_time = time.time()

        # Track current task for heartbeat status (thread-safe)
        with self._task_lock:
            self._current_task_id = task_id

        # Extract trace context from task metadata (M19)
        task_metadata = self._get_task_metadata(task_id)

        # Record queue wait time (M20) - time between enqueue and dequeue
        enqueued_at_str = task_metadata.get("enqueued_at")
        if enqueued_at_str:
            try:
                enqueued_at = datetime.fromisoformat(enqueued_at_str)
                dequeued_at = datetime.now(UTC)
                queue_wait_seconds = (dequeued_at - enqueued_at).total_seconds()
                dalston.metrics.observe_engine_queue_wait(
                    self.engine_id, queue_wait_seconds
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
                "dalston.stage": task_metadata.get("stage", "unknown"),
            },
        ):
            try:
                # Create temp directory for this task
                temp_dir = Path(tempfile.mkdtemp(prefix=self.TEMP_DIR_PREFIX))

                # Load task input from S3
                download_start = time.time()
                with dalston.telemetry.create_span("engine.download_input"):
                    task_input = self._load_task_input(task_id, temp_dir)
                dalston.metrics.observe_engine_s3_download(
                    self.engine_id, time.time() - download_start
                )
                job_id = task_input.job_id

                # Set job_id on span
                dalston.telemetry.set_span_attribute("dalston.job_id", job_id)

                # Extract request_id from task metadata and bind to logger
                request_id = task_metadata.get("request_id")
                structlog.contextvars.bind_contextvars(
                    task_id=task_id,
                    job_id=job_id,
                    engine_id=self.engine_id,
                    **({"request_id": request_id} if request_id else {}),
                )

                # Notify orchestrator that processing has started
                self._publish_task_started(task_id, job_id)

                # Process the task
                logger.info("task_processing")
                process_start = time.time()
                with dalston.telemetry.create_span("engine.process"):
                    output = self.engine.process(task_input)
                process_time = time.time() - process_start

                # Calculate total task time (for metrics)
                total_task_time = time.time() - start_time

                # Upload output to S3
                upload_start = time.time()
                with dalston.telemetry.create_span("engine.upload_output"):
                    self._save_task_output(task_id, job_id, output, total_task_time)
                dalston.metrics.observe_engine_s3_upload(
                    self.engine_id, time.time() - upload_start
                )

                # Record task success metrics (M20)
                dalston.metrics.observe_engine_task_duration(
                    self.engine_id, process_time
                )
                dalston.metrics.inc_engine_tasks(self.engine_id, "success")

                # Publish success event
                self._publish_task_completed(task_id, job_id)

                logger.info("task_completed", processing_time=round(total_task_time, 2))

            except Exception as e:
                dalston.telemetry.record_exception(e)
                dalston.telemetry.set_span_status_error(str(e))
                logger.exception("task_failed", error=str(e))

                # Record task failure metric (M20)
                dalston.metrics.inc_engine_tasks(self.engine_id, "failure")

                # We need job_id for the event, try to extract from input
                try:
                    job_id = task_input.job_id
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

    def _load_task_input(self, task_id: str, temp_dir: Path) -> TaskInput:
        """Load task input from S3 and download audio file.

        Args:
            task_id: Task identifier
            temp_dir: Temporary directory for downloaded files

        Returns:
            TaskInput with audio file path set to local temp file
        """
        # First, get the task metadata from Redis to find job_id
        # In M01, we'll use a simple convention: input is at a known location
        # The orchestrator will write task info to S3

        # For now, load from a well-known S3 path pattern
        # The orchestrator sets this up before pushing to queue
        task_metadata = self._get_task_metadata(task_id)
        job_id = task_metadata["job_id"]

        # Download task input.json from S3
        input_uri = io.build_task_input_uri(self.s3_bucket, job_id, task_id)
        input_data = io.download_json(input_uri)

        # Download audio file to temp
        # Check for media.uri (prepare stage) or audio_uri (other stages)
        media = input_data.get("media")
        audio_uri = media["uri"] if media else input_data.get("audio_uri")
        if audio_uri:
            audio_path = temp_dir / "audio.wav"
            io.download_file(audio_uri, audio_path)
        else:
            audio_path = temp_dir / "dummy.wav"
            audio_path.touch()  # Create empty file for stub engines

        return TaskInput(
            task_id=task_id,
            job_id=job_id,
            stage=task_metadata.get("stage", "unknown"),
            audio_path=audio_path,
            previous_outputs=input_data.get("previous_outputs", {}),
            config=input_data.get("config", {}),
            media=media,
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

    def _save_task_output(
        self,
        task_id: str,
        job_id: str,
        output: TaskOutput,
        processing_time: float,
    ) -> None:
        """Save task output to S3.

        Args:
            task_id: Task identifier
            job_id: Job identifier
            output: Task output from engine
            processing_time: Time taken to process in seconds
        """
        output_uri = io.build_task_output_uri(self.s3_bucket, job_id, task_id)

        output_data = {
            "task_id": task_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "processing_time_seconds": round(processing_time, 2),
            "data": output.to_dict(),
        }

        # Upload any additional artifacts
        if output.artifacts:
            artifacts_uploaded: dict[str, str] = {}
            for name, path in output.artifacts.items():
                artifact_uri = f"s3://{self.s3_bucket}/jobs/{job_id}/tasks/{task_id}/artifacts/{name}"
                io.upload_file(path, artifact_uri)
                artifacts_uploaded[name] = artifact_uri
            output_data["artifacts"] = artifacts_uploaded

        io.upload_json(output_data, output_uri)
        logger.info("output_uploaded", output_uri=output_uri)

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

        # Write to durable stream FIRST (primary mechanism)
        # Orchestrator now consumes only from durable stream, so this MUST succeed
        # Retry up to 5 times with exponential backoff on transient failures
        # If all retries fail, reconciliation sweeper will eventually detect the inconsistency
        durable_success = False
        max_retries = 5
        for attempt in range(max_retries):
            try:
                add_durable_event_sync(
                    self.redis_client,
                    "task.started",
                    {"task_id": task_id, "job_id": job_id, "engine_id": self.engine_id},
                )
                durable_success = True
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                    backoff_seconds = 0.1 * (2**attempt)
                    logger.warning(
                        "durable_event_write_retry",
                        event_type="task.started",
                        task_id=task_id,
                        attempt=attempt + 1,
                        backoff_seconds=backoff_seconds,
                        error=str(e),
                    )
                    time.sleep(backoff_seconds)
                else:
                    logger.error(
                        "durable_event_write_failed",
                        event_type="task.started",
                        task_id=task_id,
                        error=str(e),
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

        # Write to durable stream FIRST (primary mechanism)
        # Orchestrator now consumes only from durable stream, so this MUST succeed
        # Retry up to 5 times with exponential backoff on transient failures
        # If all retries fail, reconciliation sweeper will eventually detect the inconsistency
        durable_success = False
        max_retries = 5
        for attempt in range(max_retries):
            try:
                add_durable_event_sync(
                    self.redis_client,
                    "task.completed",
                    {"task_id": task_id, "job_id": job_id, "engine_id": self.engine_id},
                )
                durable_success = True
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                    backoff_seconds = 0.1 * (2**attempt)
                    logger.warning(
                        "durable_event_write_retry",
                        event_type="task.completed",
                        task_id=task_id,
                        attempt=attempt + 1,
                        backoff_seconds=backoff_seconds,
                        error=str(e),
                    )
                    time.sleep(backoff_seconds)
                else:
                    logger.error(
                        "durable_event_write_failed",
                        event_type="task.completed",
                        task_id=task_id,
                        error=str(e),
                    )

        # Publish to pub/sub for real-time delivery (secondary, for other consumers)
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))

        if not durable_success:
            # Log critical error - reconciliation sweeper will detect orphaned tasks
            # For task.completed, reconciler checks output_uri and recovers as COMPLETED
            logger.error(
                "critical_event_may_be_lost",
                event_type="task.completed",
                task_id=task_id,
                note="reconciliation_sweeper_will_recover_via_output_uri",
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

        # Write to durable stream FIRST (primary mechanism)
        # Orchestrator now consumes only from durable stream, so this MUST succeed
        # Retry up to 5 times with exponential backoff on transient failures
        # If all retries fail, reconciliation sweeper will eventually detect the inconsistency
        durable_success = False
        max_retries = 5
        for attempt in range(max_retries):
            try:
                add_durable_event_sync(
                    self.redis_client,
                    "task.failed",
                    {
                        "task_id": task_id,
                        "job_id": job_id,
                        "engine_id": self.engine_id,
                        "error": error,
                    },
                )
                durable_success = True
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                    backoff_seconds = 0.1 * (2**attempt)
                    logger.warning(
                        "durable_event_write_retry",
                        event_type="task.failed",
                        task_id=task_id,
                        attempt=attempt + 1,
                        backoff_seconds=backoff_seconds,
                        error=str(e),
                    )
                    time.sleep(backoff_seconds)
                else:
                    logger.error(
                        "durable_event_write_failed",
                        event_type="task.failed",
                        task_id=task_id,
                        error=str(e),
                    )

        # Publish to pub/sub for real-time delivery (secondary, for other consumers)
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))

        if not durable_success:
            # Log critical error - reconciliation sweeper will detect orphaned tasks
            # For task.failed, reconciler marks as FAILED (no output_uri to recover)
            logger.error(
                "critical_event_may_be_lost",
                event_type="task.failed",
                task_id=task_id,
                note="reconciliation_sweeper_will_mark_failed",
            )

        logger.debug("published_task_failed")
