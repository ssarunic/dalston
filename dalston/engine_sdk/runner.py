"""Queue polling runner for batch processing engines."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import redis

from dalston.engine_sdk import io
from dalston.engine_sdk.types import TaskInput, TaskOutput

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


logger = logging.getLogger(__name__)


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

        # Load configuration from environment
        self.engine_id = os.environ.get("ENGINE_ID", "unknown")
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        logger.info(f"Initialized EngineRunner for engine_id={self.engine_id}")

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

        logger.info(f"Starting engine loop for {self.engine_id}")
        logger.info(f"Polling queue: {self.queue_key}")

        while self._running:
            try:
                self._poll_and_process()
            except redis.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)  # Wait before reconnecting
                self._redis = None  # Force reconnection
            except Exception as e:
                logger.exception(f"Unexpected error in engine loop: {e}")
                time.sleep(1)

        logger.info("Engine loop stopped")

    def stop(self) -> None:
        """Stop the processing loop."""
        self._running = False

    def _setup_signal_handlers(self) -> None:
        """Setup handlers for graceful shutdown."""

        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
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
        logger.info(f"Received task: {task_id}")

        self._process_task(task_id)

    def _process_task(self, task_id: str) -> None:
        """Process a single task.

        Args:
            task_id: ID of the task to process
        """
        temp_dir = None
        start_time = time.time()

        try:
            # Create temp directory for this task
            temp_dir = Path(tempfile.mkdtemp(prefix=self.TEMP_DIR_PREFIX))
            logger.info(f"Created temp dir: {temp_dir}")

            # Load task input from S3
            task_input = self._load_task_input(task_id, temp_dir)
            job_id = task_input.job_id

            # Process the task
            logger.info(f"Processing task {task_id} for job {job_id}")
            output = self.engine.process(task_input)

            # Calculate processing time
            processing_time = time.time() - start_time

            # Upload output to S3
            self._save_task_output(task_id, job_id, output, processing_time)

            # Publish success event
            self._publish_task_completed(task_id, job_id)

            logger.info(
                f"Task {task_id} completed in {processing_time:.2f}s"
            )

        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")
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
                logger.debug(f"Cleaned up temp dir: {temp_dir}")

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
        audio_uri = input_data.get("audio_uri")
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
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "processing_time_seconds": round(processing_time, 2),
            "data": output.data,
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
        logger.info(f"Uploaded output to {output_uri}")

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))
        logger.debug(f"Published task.completed event for {task_id}")

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.redis_client.publish(self.EVENTS_CHANNEL, json.dumps(event))
        logger.debug(f"Published task.failed event for {task_id}")
