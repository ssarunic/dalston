import pytest

from dalston.common.queue_backends import InMemoryQueue


@pytest.mark.asyncio
async def test_in_memory_queue_enqueue_consume_ack() -> None:
    queue = InMemoryQueue()
    await queue.enqueue(stage="prepare", task_id="t1", job_id="j1", timeout_s=30)
    msg = await queue.consume(stage="prepare", consumer="c1", block_ms=50)
    assert msg is not None
    assert msg.task_id == "t1"
    await queue.ack(stage="prepare", message_id=msg.message_id)
