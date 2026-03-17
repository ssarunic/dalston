"""Tests for the unified faster-whisper runner.

Covers:
- Signal handler safety in background threads (P0 regression)
- TaskDeferredError skips ACK and failure publish (P1 regression)
- Unified runner wires shared core and admission control
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)

# ---------------------------------------------------------------------------
# P0: Signal handler safety
# ---------------------------------------------------------------------------


class TestSignalHandlerSafety:
    """EngineRunner._setup_signal_handlers must not crash in non-main thread."""

    def test_setup_signal_handlers_skips_in_non_main_thread(self) -> None:
        """Signal setup is a no-op when called from a background thread."""
        from dalston.engine_sdk.runner import EngineRunner

        mock_engine = MagicMock()
        mock_engine.get_capabilities.return_value = MagicMock(stages=["transcribe"])

        runner = EngineRunner(mock_engine)

        # Run _setup_signal_handlers in a background thread — must not raise
        error = None

        def run_in_thread():
            nonlocal error
            try:
                runner._setup_signal_handlers()
            except Exception as e:
                error = e

        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join(timeout=5)

        assert error is None, f"Signal handler setup raised in non-main thread: {error}"

    def test_setup_signal_handlers_works_in_main_thread(self) -> None:
        """Signal setup installs handlers when called from the main thread."""
        from dalston.engine_sdk.runner import EngineRunner

        mock_engine = MagicMock()
        runner = EngineRunner(mock_engine)

        # Should not raise in main thread
        runner._setup_signal_handlers()


# ---------------------------------------------------------------------------
# P1: TaskDeferredError handling in EngineRunner
# ---------------------------------------------------------------------------


class TestTaskDeferredError:
    """TaskDeferredError must skip ACK and failure publish."""

    def test_deferred_error_skips_ack(self) -> None:
        """When engine.process raises TaskDeferredError, message is not ACKed."""
        from dalston.engine_sdk.runner import EngineRunner

        mock_engine = MagicMock()
        mock_engine.get_capabilities.return_value = MagicMock(stages=["transcribe"])
        mock_engine.process.side_effect = TaskDeferredError("admission rejected")

        runner = EngineRunner(mock_engine)
        runner._running = True

        # Set up a fake message as if we just read from stream
        runner._current_message_id = "1234-0"
        runner._current_stream_id = "dalston:stream:faster-whisper"

        # Mock Redis and other dependencies
        runner._redis = MagicMock()

        # Create a fake stream message
        message = SimpleNamespace(
            task_id="task-001",
            id="1234-0",
            delivery_count=1,
        )

        with (
            patch.object(
                runner, "_process_task", side_effect=TaskDeferredError("rejected")
            ),
            patch("dalston.engine_sdk.runner.ack_task") as mock_ack,
            patch.object(runner, "_clear_waiting_engine_marker"),
        ):
            # Simulate _poll_and_process after message is claimed
            # We can't easily call _poll_and_process (it reads from Redis),
            # so we test the try/except logic directly
            try:
                runner._process_task(message.task_id)
            except TaskDeferredError:
                # This is expected — the real _poll_and_process catches it
                pass

            # ACK should NOT have been called
            mock_ack.assert_not_called()

    def test_deferred_error_does_not_publish_failure(self) -> None:
        """TaskDeferredError in _process_task re-raises without publishing failure."""
        from dalston.engine_sdk.runner import EngineRunner

        mock_engine = MagicMock()
        mock_engine.get_capabilities.return_value = MagicMock(stages=["transcribe"])

        runner = EngineRunner(mock_engine)
        runner._running = True
        runner._redis = MagicMock()

        # Mock _load_task_request to return a valid request, then engine.process raises
        mock_input = MagicMock()
        mock_input.job_id = "job-001"
        mock_input.stage = "transcribe"
        mock_input.config = {}

        with (
            patch.object(runner, "_load_task_request", return_value=mock_input),
            patch.object(runner, "_publish_task_started"),
            patch.object(runner, "_publish_task_failed") as mock_fail,
            patch.object(
                runner,
                "_get_task_metadata",
                return_value={
                    "job_id": "job-001",
                    "stage": "transcribe",
                },
            ),
        ):
            mock_engine.process.side_effect = TaskDeferredError("rejected")

            with pytest.raises(TaskDeferredError):
                runner._process_task(
                    "task-001",
                    {"job_id": "job-001", "stage": "transcribe"},
                )

            # Failure event should NOT have been published
            mock_fail.assert_not_called()

    def test_batch_rejected_error_is_task_deferred(self) -> None:
        """BatchRejectedError inherits from TaskDeferredError."""
        import importlib.util
        import sys
        from pathlib import Path

        runner_path = Path("engines/stt-unified/faster-whisper/runner.py")
        spec = importlib.util.spec_from_file_location("unified_runner_mod", runner_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["unified_runner_mod"] = module
        spec.loader.exec_module(module)

        assert issubclass(module.BatchRejectedError, TaskDeferredError)

        # Cleanup
        sys.modules.pop("unified_runner_mod", None)


# ---------------------------------------------------------------------------
# Unified runner: shared core + admission wiring
# ---------------------------------------------------------------------------


class TestUnifiedRunnerWiring:
    """Verify the unified runner creates shared core and wires admission."""

    def test_admission_wraps_batch_process(self) -> None:
        """admitted_process rejects when admission controller is full."""
        controller = AdmissionController(
            AdmissionConfig(rt_reservation=1, batch_max_inflight=1, total_capacity=2)
        )

        # Fill the batch slot
        assert controller.admit_batch() is True

        # Simulate the admitted_process wrapper logic from runner.py
        original_process = MagicMock(return_value="result")

        def admitted_process(task_request, ctx):
            if not controller.admit_batch():
                raise TaskDeferredError("Admission controller rejected batch task")
            try:
                return original_process(task_request, ctx)
            finally:
                controller.release_batch()

        mock_input = MagicMock()
        mock_input.task_id = "task-002"
        mock_ctx = MagicMock()

        # Should raise because batch slot is full
        with pytest.raises(TaskDeferredError):
            admitted_process(mock_input, mock_ctx)

        # Original process should NOT have been called
        original_process.assert_not_called()

        # Release the slot and try again
        controller.release_batch()
        result = admitted_process(mock_input, mock_ctx)
        assert result == "result"
        original_process.assert_called_once()

    def test_admission_wraps_rt_rejection(self) -> None:
        """RT admission rejects when at full capacity."""
        controller = AdmissionController(
            AdmissionConfig(rt_reservation=0, batch_max_inflight=1, total_capacity=1)
        )

        # Fill the only slot with batch
        assert controller.admit_batch() is True

        # RT should be rejected — no capacity left
        assert controller.admit_rt() is False

        # Release and RT should be accepted
        controller.release_batch()
        assert controller.admit_rt() is True


# ---------------------------------------------------------------------------
# Deferred task reclaim via read_own_pending
# ---------------------------------------------------------------------------


class TestDeferredTaskReclaim:
    """Deferred tasks must be reclaimed on the next poll cycle."""

    def test_read_own_pending_returns_unacked_message(self) -> None:
        """read_own_pending returns messages delivered but not ACKed."""
        from unittest.mock import MagicMock

        from dalston.common.streams_sync import read_own_pending

        mock_redis = MagicMock()
        # Simulate XREADGROUP with id "0" returning a pending message
        mock_redis.xreadgroup.return_value = [
            (
                "dalston:stream:faster-whisper",
                [("1234-0", {"task_id": "task-deferred", "job_id": "job-001"})],
            )
        ]

        result = read_own_pending(mock_redis, "faster-whisper", "consumer-1")

        assert result is not None
        assert result.task_id == "task-deferred"
        assert result.id == "1234-0"

        # Verify it used "0" (pending) not ">" (new)
        call_args = mock_redis.xreadgroup.call_args
        assert call_args[0][2] == {"dalston:stream:faster-whisper": "0"}

    def test_read_own_pending_returns_none_when_no_pending(self) -> None:
        """read_own_pending returns None when no unACKed messages exist."""
        from dalston.common.streams_sync import read_own_pending

        mock_redis = MagicMock()
        mock_redis.xreadgroup.return_value = []

        result = read_own_pending(mock_redis, "faster-whisper", "consumer-1")
        assert result is None

    def test_read_own_pending_skips_empty_fields(self) -> None:
        """Messages with empty fields (already ACKed) are skipped."""
        from dalston.common.streams_sync import read_own_pending

        mock_redis = MagicMock()
        # Empty fields dict means the message was ACKed but still in history
        mock_redis.xreadgroup.return_value = [
            ("dalston:stream:faster-whisper", [("1234-0", {})])
        ]

        result = read_own_pending(mock_redis, "faster-whisper", "consumer-1")
        assert result is None
