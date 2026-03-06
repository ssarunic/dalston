"""Unit tests for orchestrator durable event reliability policy (M54)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.common.durable_events import (
    FAILURE_REASON_HANDLER_EXCEPTION,
    FAILURE_REASON_INVALID_PAYLOAD_JSON,
    FAILURE_REASON_UNKNOWN_EVENT_TYPE,
    DurableEventEnvelope,
)
from dalston.config import Settings
from dalston.orchestrator.main import (
    HandlerExecutionError,
    UnknownEventTypeError,
    _process_durable_event,
)


def _settings(max_deliveries: int = 3) -> Settings:
    return Settings(
        events_max_deliveries=max_deliveries,
        events_dlq_stream="dalston:events:dlq",
        events_dlq_maxlen=1000,
    )


def _valid_envelope(*, delivery_count: int = 1) -> DurableEventEnvelope:
    return DurableEventEnvelope(
        message_id="1234567890-0",
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
async def test_retryable_failure_below_threshold_remains_pending():
    envelope = _valid_envelope(delivery_count=2)
    settings = _settings(max_deliveries=3)

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict",
            new=AsyncMock(side_effect=HandlerExecutionError("handler exploded")),
        ),
        patch("dalston.orchestrator.main.ack_event", new=AsyncMock()) as mock_ack,
        patch(
            "dalston.orchestrator.main.move_event_to_dlq", new=AsyncMock()
        ) as mock_move_to_dlq,
        patch(
            "dalston.orchestrator.distributed_main.dalston.metrics.inc_orchestrator_event_decision",
            new=MagicMock(),
        ) as mock_decision_metric,
    ):
        await _process_durable_event(
            envelope,
            redis=AsyncMock(),
            settings=settings,
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )

    mock_ack.assert_not_called()
    mock_move_to_dlq.assert_not_called()
    mock_decision_metric.assert_called_once_with(
        decision="retry",
        failure_reason=FAILURE_REASON_HANDLER_EXCEPTION,
        event_type="task.completed",
    )


@pytest.mark.asyncio
async def test_retryable_failure_at_threshold_moves_to_dlq():
    envelope = _valid_envelope(delivery_count=3)
    settings = _settings(max_deliveries=3)

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict",
            new=AsyncMock(side_effect=HandlerExecutionError("handler exploded")),
        ),
        patch("dalston.orchestrator.main.ack_event", new=AsyncMock()) as mock_ack,
        patch(
            "dalston.orchestrator.main.move_event_to_dlq",
            new=AsyncMock(return_value="dlq-1"),
        ) as mock_move_to_dlq,
        patch(
            "dalston.orchestrator.distributed_main.dalston.metrics.inc_orchestrator_event_decision",
            new=MagicMock(),
        ) as mock_decision_metric,
    ):
        await _process_durable_event(
            envelope,
            redis=AsyncMock(),
            settings=settings,
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )

    mock_ack.assert_not_called()
    mock_move_to_dlq.assert_awaited_once()
    assert (
        mock_move_to_dlq.await_args.kwargs["failure_reason"]
        == FAILURE_REASON_HANDLER_EXCEPTION
    )
    mock_decision_metric.assert_called_once_with(
        decision="dlq",
        failure_reason=FAILURE_REASON_HANDLER_EXCEPTION,
        event_type="task.completed",
    )


@pytest.mark.asyncio
async def test_malformed_event_is_quarantined_immediately():
    envelope = DurableEventEnvelope(
        message_id="1234567890-0",
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
        patch("dalston.orchestrator.main.ack_event", new=AsyncMock()) as mock_ack,
        patch(
            "dalston.orchestrator.main.move_event_to_dlq",
            new=AsyncMock(return_value="dlq-1"),
        ) as mock_move_to_dlq,
    ):
        await _process_durable_event(
            envelope,
            redis=AsyncMock(),
            settings=_settings(),
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )

    mock_dispatch.assert_not_called()
    mock_ack.assert_not_called()
    mock_move_to_dlq.assert_awaited_once()
    assert (
        mock_move_to_dlq.await_args.kwargs["failure_reason"]
        == FAILURE_REASON_INVALID_PAYLOAD_JSON
    )


@pytest.mark.asyncio
async def test_unknown_event_type_routes_to_dlq():
    envelope = _valid_envelope(delivery_count=1)
    envelope.event_type = "unknown.custom"

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict",
            new=AsyncMock(side_effect=UnknownEventTypeError("unknown.custom")),
        ),
        patch("dalston.orchestrator.main.ack_event", new=AsyncMock()) as mock_ack,
        patch(
            "dalston.orchestrator.main.move_event_to_dlq",
            new=AsyncMock(return_value="dlq-1"),
        ) as mock_move_to_dlq,
    ):
        await _process_durable_event(
            envelope,
            redis=AsyncMock(),
            settings=_settings(),
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="crash_recovery",
        )

    mock_ack.assert_not_called()
    mock_move_to_dlq.assert_awaited_once()
    assert (
        mock_move_to_dlq.await_args.kwargs["failure_reason"]
        == FAILURE_REASON_UNKNOWN_EVENT_TYPE
    )


@pytest.mark.asyncio
async def test_successful_event_is_acked():
    envelope = _valid_envelope(delivery_count=1)

    with (
        patch(
            "dalston.orchestrator.main._dispatch_event_dict", new=AsyncMock()
        ) as mock_dispatch,
        patch("dalston.orchestrator.main.ack_event", new=AsyncMock()) as mock_ack,
        patch(
            "dalston.orchestrator.main.move_event_to_dlq", new=AsyncMock()
        ) as mock_move_to_dlq,
        patch(
            "dalston.orchestrator.distributed_main.dalston.metrics.inc_orchestrator_event_decision",
            new=MagicMock(),
        ) as mock_decision_metric,
    ):
        await _process_durable_event(
            envelope,
            redis=AsyncMock(),
            settings=_settings(),
            batch_registry=AsyncMock(),
            consumer_id="orchestrator-test",
            source="live_consumer",
        )

    mock_dispatch.assert_awaited_once()
    mock_ack.assert_awaited_once()
    mock_move_to_dlq.assert_not_called()
    mock_decision_metric.assert_called_once_with(
        decision="ack",
        failure_reason="none",
        event_type="task.completed",
    )
