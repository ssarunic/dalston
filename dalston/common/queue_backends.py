"""Mode-aware task queue backends for distributed (Redis) and lite (in-memory) runtimes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from dalston.common.streams import ack_task, add_task, read_task


@dataclass
class QueueEnvelope:
    message_id: str
    stage: str
    task_id: str
    job_id: str


class TaskQueue(Protocol):
    async def enqueue(
        self, *, stage: str, task_id: str, job_id: str, timeout_s: int
    ) -> str: ...

    async def consume(
        self, *, stage: str, consumer: str, block_ms: int = 30000
    ) -> QueueEnvelope | None: ...

    async def ack(self, *, stage: str, message_id: str) -> None: ...


class RedisStreamsQueue(TaskQueue):
    """Distributed queue adapter backed by Redis Streams."""

    def __init__(self, redis: Redis):
        self._redis = redis

    async def enqueue(
        self, *, stage: str, task_id: str, job_id: str, timeout_s: int
    ) -> str:
        return await add_task(
            self._redis,
            stage=stage,
            task_id=task_id,
            job_id=job_id,
            timeout_s=timeout_s,
        )

    async def consume(
        self, *, stage: str, consumer: str, block_ms: int = 30000
    ) -> QueueEnvelope | None:
        msg = await read_task(
            self._redis, stage=stage, consumer=consumer, block_ms=block_ms
        )
        if msg is None:
            return None
        return QueueEnvelope(
            message_id=msg.id,
            stage=stage,
            task_id=msg.task_id,
            job_id=msg.job_id,
        )

    async def ack(self, *, stage: str, message_id: str) -> None:
        await ack_task(self._redis, stage=stage, message_id=message_id)


class InMemoryQueue(TaskQueue):
    """Single-process in-memory queue with explicit ack lifecycle."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[QueueEnvelope]] = {}
        self._in_flight: dict[str, QueueEnvelope] = {}
        self._counter = 0

    def _queue(self, stage: str) -> asyncio.Queue[QueueEnvelope]:
        if stage not in self._queues:
            self._queues[stage] = asyncio.Queue()
        return self._queues[stage]

    async def enqueue(
        self, *, stage: str, task_id: str, job_id: str, timeout_s: int
    ) -> str:
        self._counter += 1
        message_id = f"{self._counter}-0"
        envelope = QueueEnvelope(
            message_id=message_id,
            stage=stage,
            task_id=task_id,
            job_id=job_id,
        )
        await self._queue(stage).put(envelope)
        return message_id

    async def consume(
        self, *, stage: str, consumer: str, block_ms: int = 30000
    ) -> QueueEnvelope | None:
        timeout_s = block_ms / 1000
        try:
            envelope = await asyncio.wait_for(
                self._queue(stage).get(), timeout=timeout_s
            )
        except TimeoutError:
            return None
        self._in_flight[envelope.message_id] = envelope
        return envelope

    async def ack(self, *, stage: str, message_id: str) -> None:
        self._in_flight.pop(message_id, None)
