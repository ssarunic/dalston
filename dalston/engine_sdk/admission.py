"""Admission controller for mixed batch + realtime engine workloads.

Prevents realtime session starvation under batch load by reserving
capacity for RT sessions and capping concurrent batch tasks.

Configuration via environment variables:
    DALSTON_RT_RESERVATION: Minimum slots reserved for realtime (default: 2)
    DALSTON_BATCH_MAX_INFLIGHT: Maximum concurrent batch tasks (default: 4)
    DALSTON_TOTAL_CAPACITY: Total engine capacity (default: 6)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Lock

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class AdmissionConfig:
    """Configuration for the admission controller."""

    rt_reservation: int = 2
    batch_max_inflight: int = 4
    total_capacity: int = 6

    @classmethod
    def from_env(cls) -> AdmissionConfig:
        """Create config from environment variables."""
        return cls(
            rt_reservation=int(os.environ.get("DALSTON_RT_RESERVATION", "2")),
            batch_max_inflight=int(os.environ.get("DALSTON_BATCH_MAX_INFLIGHT", "4")),
            total_capacity=int(os.environ.get("DALSTON_TOTAL_CAPACITY", "6")),
        )


class AdmissionController:
    """Prevents realtime starvation under batch load.

    The controller tracks active batch and realtime work items and
    enforces two constraints:

    1. **RT reservation**: A minimum number of slots are always kept
       available for realtime sessions. Batch tasks are rejected if
       accepting them would consume reserved RT capacity.

    2. **Batch cap**: A hard limit on concurrent batch tasks prevents
       a flood of batch work from monopolizing the engine.

    Usage:
        controller = AdmissionController(AdmissionConfig.from_env())

        # Batch adapter checks before accepting work
        if controller.can_accept_batch():
            controller.admit_batch()
            try:
                process_batch_task(task)
            finally:
                controller.release_batch()
        else:
            nack_task(task)  # Return to queue

        # RT adapter checks before accepting session
        if controller.can_accept_rt():
            controller.admit_rt()
            try:
                handle_session(session)
            finally:
                controller.release_rt()
        else:
            reject_with_503(session)
    """

    def __init__(self, config: AdmissionConfig) -> None:
        self._config = config
        self._lock = Lock()
        self._active_batch: int = 0
        self._active_rt: int = 0

        logger.info(
            "admission_controller_init",
            rt_reservation=config.rt_reservation,
            batch_max_inflight=config.batch_max_inflight,
            total_capacity=config.total_capacity,
        )

    @property
    def config(self) -> AdmissionConfig:
        return self._config

    # -- Query methods (thread-safe) -----------------------------------------

    def can_accept_batch(self) -> bool:
        """Check if a new batch task can be admitted.

        Rejects batch if:
        - At the inflight cap for batch tasks
        - Accepting would consume capacity reserved for RT
        """
        with self._lock:
            return self._can_accept_batch_unlocked()

    def can_accept_rt(self) -> bool:
        """Check if a new realtime session can be admitted.

        RT always gets its reserved slots. Beyond that, it shares
        remaining capacity with batch tasks.
        """
        with self._lock:
            return self._can_accept_rt_unlocked()

    # -- Admission methods (thread-safe) -------------------------------------

    def admit_batch(self) -> bool:
        """Admit a batch task. Returns True if admitted, False if rejected."""
        with self._lock:
            if not self._can_accept_batch_unlocked():
                return False
            self._active_batch += 1
            logger.debug(
                "batch_admitted",
                active_batch=self._active_batch,
                active_rt=self._active_rt,
            )
            return True

    def admit_rt(self) -> bool:
        """Admit a realtime session. Returns True if admitted, False if rejected."""
        with self._lock:
            if not self._can_accept_rt_unlocked():
                return False
            self._active_rt += 1
            logger.debug(
                "rt_admitted",
                active_batch=self._active_batch,
                active_rt=self._active_rt,
            )
            return True

    def release_batch(self) -> None:
        """Release a batch task slot."""
        with self._lock:
            self._active_batch = max(0, self._active_batch - 1)
            logger.debug(
                "batch_released",
                active_batch=self._active_batch,
                active_rt=self._active_rt,
            )

    def release_rt(self) -> None:
        """Release a realtime session slot."""
        with self._lock:
            self._active_rt = max(0, self._active_rt - 1)
            logger.debug(
                "rt_released",
                active_batch=self._active_batch,
                active_rt=self._active_rt,
            )

    # -- Status --------------------------------------------------------------

    def get_status(self) -> dict:
        """Get current admission status for monitoring."""
        with self._lock:
            total = self._active_batch + self._active_rt
            return {
                "active_batch": self._active_batch,
                "active_rt": self._active_rt,
                "total_active": total,
                "total_capacity": self._config.total_capacity,
                "available": self._config.total_capacity - total,
                "can_accept_batch": self._can_accept_batch_unlocked(),
                "can_accept_rt": self._can_accept_rt_unlocked(),
                "rt_reservation": self._config.rt_reservation,
                "batch_max_inflight": self._config.batch_max_inflight,
            }

    # -- Internal (must be called with lock held) ----------------------------

    def _can_accept_batch_unlocked(self) -> bool:
        # Hard cap on batch inflight
        if self._active_batch >= self._config.batch_max_inflight:
            return False

        total = self._active_batch + self._active_rt
        available = self._config.total_capacity - total

        if available <= 0:
            return False

        # Don't consume capacity reserved for RT unless RT already
        # has its reserved slots filled
        if self._active_rt >= self._config.rt_reservation:
            # RT has enough sessions, batch can use remaining
            return True

        # RT hasn't filled its reservation yet — only accept batch
        # if there's capacity beyond the RT reservation
        rt_shortfall = self._config.rt_reservation - self._active_rt
        return available > rt_shortfall

    def _can_accept_rt_unlocked(self) -> bool:
        total = self._active_batch + self._active_rt
        return total < self._config.total_capacity


class TaskDeferredError(Exception):
    """Raised when a task should be deferred, not failed.

    The EngineRunner catches this and skips both the failure event publish
    and the stream ACK, leaving the message in the PEL for later redelivery
    (either by the same engine after backoff or by another instance via
    stale-task claiming).
    """
