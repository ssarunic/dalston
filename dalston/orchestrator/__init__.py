"""Dalston Orchestrator - Job expansion and task scheduling.

The orchestrator listens to Redis pub/sub events and:
- Expands jobs into task DAGs
- Schedules tasks to engine queues
- Handles task completion/failure
- Manages job lifecycle
"""

from dalston.orchestrator.dag import build_task_dag
from dalston.orchestrator.handlers import (
    handle_job_created,
    handle_task_completed,
    handle_task_failed,
)
from dalston.orchestrator.scheduler import queue_task

__all__ = [
    "build_task_dag",
    "handle_job_created",
    "handle_task_completed",
    "handle_task_failed",
    "queue_task",
]
