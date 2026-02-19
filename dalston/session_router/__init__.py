"""Session Router for real-time transcription worker pool management.

The Session Router manages the pool of real-time transcription workers,
handling session allocation, health monitoring, and capacity management.

Example usage (embedded in Gateway):
    from dalston.session_router import SessionRouter

    router = SessionRouter(redis_url="redis://localhost:6379")

    # On application startup
    await router.start()

    # Acquire worker for incoming WebSocket connection
    allocation = await router.acquire_worker(
        language="en",
        model=None,  # None = auto, or specific model name
        client_ip="192.168.1.100"
    )

    if allocation:
        # Proxy client to worker.endpoint
        print(f"Allocated: {allocation.endpoint}, session: {allocation.session_id}")
    else:
        # No capacity available
        return error_response("no_capacity")

    # Release when session ends
    await router.release_worker(allocation.session_id)

    # On application shutdown
    await router.stop()

API endpoints (for management):
    GET /v1/realtime/status - Get capacity info
    GET /v1/realtime/workers - List all workers
    GET /v1/realtime/sessions - List active sessions
"""

from dalston.session_router.allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.session_router.health import HealthMonitor
from dalston.session_router.registry import WorkerRegistry, WorkerState
from dalston.session_router.router import CapacityInfo, SessionRouter, WorkerStatus

__all__ = [
    # Main router
    "SessionRouter",
    # Types
    "WorkerAllocation",
    "WorkerStatus",
    "WorkerState",
    "SessionState",
    "CapacityInfo",
    # Components (for advanced usage)
    "WorkerRegistry",
    "SessionAllocator",
    "HealthMonitor",
]
