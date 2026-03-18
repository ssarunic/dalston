"""Mixed-load benchmark harness for unified engine admission control.

Tests concurrent batch and RT load against a shared AdmissionController
to verify QoS properties:
- RT tasks are never starved under batch load
- Batch throughput degrades gracefully under RT pressure
- No deadlocks or admission leaks under concurrent access

Usage:
    pytest tests/benchmarks/test_mixed_load.py -m benchmark --tb=short
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock

import pytest

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)


@pytest.mark.benchmark
class TestMixedLoadBenchmark:
    """Benchmark concurrent batch + RT load through AdmissionController."""

    @staticmethod
    def _make_controller(
        rt_reservation: int = 2,
        batch_max_inflight: int = 4,
        total_capacity: int = 6,
    ) -> AdmissionController:
        return AdmissionController(
            AdmissionConfig(
                rt_reservation=rt_reservation,
                batch_max_inflight=batch_max_inflight,
                total_capacity=total_capacity,
            )
        )

    def test_batch_throughput_baseline(self) -> None:
        """Measure batch admission throughput without RT contention."""
        controller = self._make_controller()
        n_tasks = 200
        durations: list[float] = []

        for _ in range(n_tasks):
            t0 = time.monotonic()
            admitted = controller.admit_batch()
            dt = time.monotonic() - t0
            if admitted:
                durations.append(dt)
                controller.release_batch()

        assert len(durations) == n_tasks
        p99 = sorted(durations)[int(len(durations) * 0.99)] * 1e6
        assert p99 < 1000, f"p99 admission latency {p99:.0f}µs exceeds 1ms"

    def test_rt_never_starved_under_batch_load(self) -> None:
        """RT admits succeed even when batch slots are saturated."""
        controller = self._make_controller(
            rt_reservation=2, batch_max_inflight=4, total_capacity=6
        )
        rt_admits = 0
        rt_rejects = 0
        batch_admits = 0
        stop = threading.Event()

        def batch_worker():
            nonlocal batch_admits
            while not stop.is_set():
                if controller.admit_batch():
                    batch_admits += 1
                    time.sleep(0.001)  # simulate 1ms processing
                    controller.release_batch()

        # Start batch workers to saturate batch slots
        threads = [threading.Thread(target=batch_worker, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()

        # Let batch workers saturate
        time.sleep(0.05)

        # Try RT admits while batch is saturated
        for _ in range(50):
            if controller.admit_rt():
                rt_admits += 1
                controller.release_rt()
            else:
                rt_rejects += 1
            time.sleep(0.001)

        stop.set()
        for t in threads:
            t.join(timeout=2)

        # RT should succeed most of the time (reserved slots)
        assert rt_admits > 0, "RT was completely starved"
        rt_success_rate = rt_admits / (rt_admits + rt_rejects)
        assert rt_success_rate > 0.8, (
            f"RT success rate {rt_success_rate:.1%} below 80% threshold"
        )

    def test_no_admission_leaks_under_concurrent_access(self) -> None:
        """All slots are released after concurrent batch+RT workload."""
        controller = self._make_controller(
            rt_reservation=2, batch_max_inflight=4, total_capacity=6
        )
        errors: list[Exception] = []

        def batch_worker(n_iterations: int):
            for _ in range(n_iterations):
                if controller.admit_batch():
                    time.sleep(0.0001)
                    controller.release_batch()

        def rt_worker(n_iterations: int):
            for _ in range(n_iterations):
                if controller.admit_rt():
                    time.sleep(0.0001)
                    controller.release_rt()

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for _ in range(4):
                futures.append(pool.submit(batch_worker, 100))
            for _ in range(4):
                futures.append(pool.submit(rt_worker, 100))

            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    errors.append(exc)

        assert not errors, f"Errors during concurrent access: {errors}"

        # After all workers complete, all slots should be free
        status = controller.get_status()
        assert status["active_batch"] == 0
        assert status["active_rt"] == 0

    def test_batch_rejection_rate_under_full_load(self) -> None:
        """Measure batch rejection rate when all slots are contended."""
        controller = self._make_controller(
            rt_reservation=2, batch_max_inflight=2, total_capacity=4
        )
        admitted = 0
        rejected = 0
        lock = threading.Lock()

        def batch_attempt():
            nonlocal admitted, rejected
            if controller.admit_batch():
                with lock:
                    admitted += 1
                time.sleep(0.005)  # hold slot for 5ms
                controller.release_batch()
            else:
                with lock:
                    rejected += 1

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(batch_attempt) for _ in range(100)]
            for f in as_completed(futures):
                f.result()

        total = admitted + rejected
        assert total == 100
        # Some rejections are expected when contending for 2 slots with 8 threads
        assert admitted > 0, "No batch tasks were admitted"

    def test_admitted_process_wrapper_pattern(self) -> None:
        """Verify the admitted_process wrapper used in unified runner."""
        controller = self._make_controller(
            rt_reservation=1, batch_max_inflight=1, total_capacity=2
        )
        mock_process = MagicMock(return_value="result")

        def admitted_process(task_request, ctx):
            if not controller.admit_batch():
                raise TaskDeferredError("Admission controller rejected batch task")
            try:
                return mock_process(task_request, ctx)
            finally:
                controller.release_batch()

        # Fill the batch slot
        assert controller.admit_batch() is True

        # Second batch task should be deferred
        with pytest.raises(TaskDeferredError):
            admitted_process("input", "ctx")

        mock_process.assert_not_called()

        # Release and retry
        controller.release_batch()
        result = admitted_process("input", "ctx")
        assert result == "result"
        mock_process.assert_called_once()
