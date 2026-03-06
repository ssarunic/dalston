"""Integration tests for M54 durable event DLQ recovery policy."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.common.durable_events import (
    FAILURE_REASON_INVALID_PAYLOAD_JSON,
    DurableEventEnvelope,
)
from dalston.config import Settings
from dalston.orchestrator.main import HandlerExecutionError, _process_durable_event


class _InMemoryRedis:
    """Minimal async Redis stub for xadd/xack integration assertions."""

    def __init__(self) -> None:
        self.xadd_calls: list[dict] = []
        self.xack_calls: list[dict] = []

    async def xadd(
        self,
        stream: str,
        fields: dict,
        maxlen: int | None = None,
        approximate: bool | None = None,
    ) -> str:
        self.xadd_calls.append(
            {
                "stream": stream,
                "fields": fields,
                "maxlen": maxlen,
                "approximate": approximate,
            }
        )
        return f"{len(self.xadd_calls)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.xack_calls.append(
            {
                "stream": stream,
                "group": group,
                "message_id": message_id,
            }
        )
        return 1


def _settings(max_deliveries: int = 2) -> Settings:
    return Settings(
        events_max_deliveries=max_deliveries,
        events_dlq_stream="dalston:events:dlq",
        events_dlq_maxlen=1000,
    )


def _event(message_id: str, delivery_count: int = 1) -> DurableEventEnvelope:
    return DurableEventEnvelope(
        message_id=message_id,
        event_type="task.completed",
        timestamp="2026-03-05T00:00:00+00:00",
        payload={"task_id": "8f56a448-95df-4cb2-b2cd-314e06a34726"},
        raw_fields={
            "type": "task.completed",
            "payload": '{"task_id":"8f56a448-95df-4cb2-b2cd-314e06a34726"}',
        },
        raw_payload='{"task_id":"8f56a448-95df-4cb2-b2cd-314e06a34726"}',
        delivery_count=delivery_count,
    )


@pytest.mark.asyncio
async def test_poison_event_quarantined_while_healthy_event_continues():
    """Poison event should be DLQd at threshold while healthy event still ACKs."""
    redis = _InMemoryRedis()
    settings = _settings(max_deliveries=2)

    poison = _event("111-0", delivery_count=2)
    healthy = _event("222-0", delivery_count=1)

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict",
            new=AsyncMock(side_effect=[HandlerExecutionError("forced failure"), None]),
        ),
        patch(
            "dalston.orchestrator.distributed_main.dalston.metrics.inc_orchestrator_event_decision",
            new=MagicMock(),
        ),
    ):
        await _process_durable_event(
            poison,
            redis=redis,  # type: ignore[arg-type]
            settings=settings,
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )
        await _process_durable_event(
            healthy,
            redis=redis,  # type: ignore[arg-type]
            settings=settings,
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )

    assert len(redis.xadd_calls) == 1
    assert redis.xadd_calls[0]["stream"] == "dalston:events:dlq"
    assert redis.xadd_calls[0]["fields"]["source_message_id"] == "111-0"
    assert redis.xadd_calls[0]["fields"]["failure_reason"] == "handler_exception"

    assert len(redis.xack_calls) == 2
    assert redis.xack_calls[0]["message_id"] == "111-0"  # poison, after DLQ write
    assert redis.xack_calls[1]["message_id"] == "222-0"  # healthy ACK path


@pytest.mark.asyncio
async def test_malformed_event_is_dlqd_without_dispatch_attempt():
    """Malformed event should bypass dispatch and be DLQd on first handling."""
    redis = _InMemoryRedis()

    malformed = DurableEventEnvelope(
        message_id="333-0",
        event_type="task.completed",
        delivery_count=1,
        raw_fields={"type": "task.completed", "payload": "{not-json"},
        raw_payload="{not-json",
        failure_reason=FAILURE_REASON_INVALID_PAYLOAD_JSON,
        error="Expecting property name enclosed in double quotes",
    )

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict", new=AsyncMock()
        ) as mock_dispatch,
        patch(
            "dalston.orchestrator.distributed_main.dalston.metrics.inc_orchestrator_event_decision",
            new=MagicMock(),
        ),
    ):
        await _process_durable_event(
            malformed,
            redis=redis,  # type: ignore[arg-type]
            settings=_settings(max_deliveries=5),
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="crash_recovery",
        )

    mock_dispatch.assert_not_called()
    assert len(redis.xadd_calls) == 1
    assert redis.xadd_calls[0]["fields"]["failure_reason"] == "invalid_payload_json"
    assert len(redis.xack_calls) == 1
    assert redis.xack_calls[0]["message_id"] == "333-0"
