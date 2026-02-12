"""Queue polling runner for batch processing engines."""

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

import redis
import structlog

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.engine_sdk import io
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
    2. Polls the engine's queue (dalston:queue:{engine_id})
    3. Downloads task input from S3
    4. Calls engine.process()
    5. Uploads task output to S3
    6. Publishes completion/failure events
    7. Cleans up temp files
    """

    # Redis key patterns
    QUEUE_KEY = "dalston:queue:{engine_id}"
    EVENTS_CHANNEL = "dalston:events"

    # Configuration
    QUEUE_POLL_TIMEOUT = 30  # seconds
    TEMP_DIR_PREFIX = "dalston_task_"

    def __init__(self, engine: Engine) -> None:
        """Initialize the runner.

        Args:
            engine: Engine instance to run tasks with
        """
        self.engine = engine
        self._redis: redis.Redis | None = None
        self._running = False
        self._metrics_server: HTTPServer | None = None
        self._metrics_thread: threading.Thread | None = None

        # Load configuration from environment
        self.engine_id = os.environ.get("ENGINE_ID", "unknown")
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        self.metrics_port = int(os.environ.get("METRICS_PORT", "9100"))

        # Configure structured logging
        dalston.logging.configure(f"engine-{self.engine_id}")

        # Configure distributed tracing (M19)
        dalston.telemetry.configure_tracing(f"dalston-engine-{self.engine_id}")

        # Configure Prometheus metrics (M20)
        dalston.metrics.configure_metrics(f"engine-{self.engine_id}")

        logger.info("engine_runner_initialized", engine_id=self.engine_id)

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
    def queue_key(self) -> str:
        """Get the Redis queue key for this engine."""
        return self.QUEUE_KEY.format(engine_id=self.engine_id)

    def run(self) -> None:
        """Start the processing loop.

        This method blocks until the engine is stopped via SIGTERM/SIGINT.
        """
        self._running = True
        self._setup_signal_handlers()

        # Start metrics HTTP server in background thread (M20)
        self._start_metrics_server()

        logger.info(
            "engine_loop_starting", engine_id=self.engine_id, queue=self.queue_key
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
            self._stop_metrics_server()
            dalston.telemetry.shutdown_tracing()
            logger.info("engine_loop_stopped")

    def stop(self) -> None:
        """Stop the processing loop."""
        self._running = False

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

    def _setup_signal_handlers(self) -> None:
        """Setup handlers for graceful shutdown."""

        def handle_signal(signum, frame):
            logger.info("shutdown_signal_received", signal=signum)
            self.stop()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    def _poll_and_process(self) -> None:
        """Poll the queue and process one task."""
        # Blocking pop with timeout
        result = self.redis_client.brpop(
            self.queue_key,
            timeout=self.QUEUE_POLL_TIMEOUT,
        )

        if result is None:
            # Timeout, no task available
            return

        _, task_id = result
        logger.info("task_received", task_id=task_id)

        self._process_task(task_id)

    def _process_task(self, task_id: str) -> None:
        """Process a single task.

        Args:
            task_id: ID of the task to process
        """
        temp_dir = None
        start_time = time.time()

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
        """Publish task.started event to Redis pub/sub.

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
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))
        logger.debug("published_task_started")

    def _publish_task_completed(self, task_id: str, job_id: str) -> None:
        """Publish task.completed event to Redis pub/sub.

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
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))
        logger.debug("published_task_completed")

    def _publish_task_failed(
        self,
        task_id: str,
        job_id: str,
        error: str,
    ) -> None:
        """Publish task.failed event to Redis pub/sub.

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
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))
        logger.debug("published_task_failed")
