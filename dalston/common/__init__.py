from typing import Any

try:  # pragma: no cover - exercised in dependency-constrained test envs
    from redis.asyncio import Redis
except ModuleNotFoundError:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from dalston.common.models import Job, JobStatus, Task, TaskStatus
from dalston.common.redis import close_redis, get_redis

__all__ = [
    "Job",
    "Task",
    "JobStatus",
    "TaskStatus",
    "get_redis",
    "close_redis",
    "Redis",
]
