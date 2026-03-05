"""Unit tests for durable events stream helpers (M54)."""

import json
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ResponseError


class TestEnsureEventsStreamGroup:
    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_creates_group(self, mock_redis):
        from dalston.common.durable_events import ensure_events_stream_group

        mock_redis.xgroup_create = AsyncMock()

        await ensure_events_stream_group(mock_redis)

        mock_redis.xgroup_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_existing_group(self, mock_redis):
        from dalston.common.durable_events import ensure_events_stream_group

        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )

        await ensure_events_stream_group(mock_redis)


class TestAddDurableEvent:
    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xadd = AsyncMock(return_value="1234567890-0")
        return redis

    @pytest.mark.asyncio
    async def test_adds_event_to_stream(self, mock_redis):
        from dalston.common.durable_events import EVENTS_STREAM, add_durable_event

        msg_id = await add_durable_event(
            mock_redis,
            "job.created",
            {"job_id": "test-job-123"},
        )

        assert msg_id == "1234567890-0"
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == EVENTS_STREAM

    @pytest.mark.asyncio
    async def test_event_contains_type_timestamp_and_json_payload(self, mock_redis):
        from dalston.common.durable_events import add_durable_event

        await add_durable_event(
            mock_redis,
            "task.completed",
            {"task_id": "task-456"},
        )

        event_data = mock_redis.xadd.call_args[0][1]
        assert event_data["type"] == "task.completed"
        assert "timestamp" in event_data
        assert json.loads(event_data["payload"]) == {"task_id": "task-456"}


class TestReadNewEvents:
    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_reads_valid_event_envelope(self, mock_redis):
        from dalston.common.durable_events import read_new_events

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                (
                    "dalston:events:stream",
                    [
                        (
                            "1234567890-0",
                            {
                                "type": "task.completed",
                                "timestamp": "2024-01-01T00:00:00+00:00",
                                "payload": '{"task_id":"task-456"}',
                            },
                        )
                    ],
                )
            ]
        )

        events = await read_new_events(mock_redis, "consumer-1")

        assert len(events) == 1
        envelope = events[0]
        assert envelope.message_id == "1234567890-0"
        assert envelope.event_type == "task.completed"
        assert envelope.payload == {"task_id": "task-456"}
        assert envelope.delivery_count == 1
        assert envelope.is_valid

    @pytest.mark.asyncio
    async def test_marks_invalid_payload_json_as_non_retryable(self, mock_redis):
        from dalston.common.durable_events import (
            FAILURE_REASON_INVALID_PAYLOAD_JSON,
            read_new_events,
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                (
                    "dalston:events:stream",
                    [
                        (
                            "1234567890-0",
                            {
                                "type": "task.completed",
                                "timestamp": "2024-01-01T00:00:00+00:00",
                                "payload": "{not-json",
                            },
                        )
                    ],
                )
            ]
        )

        events = await read_new_events(mock_redis, "consumer-1")

        assert len(events) == 1
        envelope = events[0]
        assert not envelope.is_valid
        assert envelope.failure_reason == FAILURE_REASON_INVALID_PAYLOAD_JSON

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self, mock_redis):
        from dalston.common.durable_events import read_new_events

        mock_redis.xreadgroup = AsyncMock(return_value=None)

        events = await read_new_events(mock_redis, "consumer-1")
        assert events == []


class TestClaimStalePendingEvents:
    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_claims_stale_events_with_xpending_delivery_count(self, mock_redis):
        from dalston.common.durable_events import claim_stale_pending_events

        mock_redis.xautoclaim = AsyncMock(
            return_value=(
                "0-0",
                [
                    (
                        "1234567890-0",
                        {
                            "type": "job.created",
                            "timestamp": "2024-01-01T00:00:00+00:00",
                            "payload": '{"job_id":"test-123"}',
                        },
                    )
                ],
                [],
            )
        )
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234567890-0",
                    "consumer": "consumer-1",
                    "time_since_delivered": 1000,
                    "times_delivered": 3,
                }
            ]
        )

        events = await claim_stale_pending_events(mock_redis, "new-consumer")

        assert len(events) == 1
        assert events[0].delivery_count == 3
        assert events[0].payload == {"job_id": "test-123"}

    @pytest.mark.asyncio
    async def test_claim_uses_embedded_delivery_count_metadata_when_available(
        self, mock_redis
    ):
        from dalston.common.durable_events import claim_stale_pending_events

        mock_redis.xautoclaim = AsyncMock(
            return_value=(
                "0-0",
                [
                    (
                        "1234567890-0",
                        {
                            "type": "job.created",
                            "timestamp": "2024-01-01T00:00:00+00:00",
                            "payload": '{"job_id":"test-123"}',
                        },
                        {"times_delivered": 4},
                    )
                ],
                [],
            )
        )

        events = await claim_stale_pending_events(mock_redis, "new-consumer")

        assert len(events) == 1
        assert events[0].delivery_count == 4
        mock_redis.xpending_range.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_nogroup_error(self, mock_redis):
        from dalston.common.durable_events import claim_stale_pending_events

        mock_redis.xautoclaim = AsyncMock(
            side_effect=ResponseError("NOGROUP No such consumer group")
        )

        events = await claim_stale_pending_events(mock_redis, "new-consumer")

        assert events == []
        mock_redis.xgroup_create.assert_called()


class TestAckAndDlq:
    @pytest.fixture
    def envelope(self):
        from dalston.common.durable_events import DurableEventEnvelope

        return DurableEventEnvelope(
            message_id="1234567890-0",
            event_type="task.completed",
            timestamp="2024-01-01T00:00:00+00:00",
            payload={"task_id": "task-123"},
            raw_fields={"type": "task.completed", "payload": '{"task_id":"task-123"}'},
            raw_payload='{"task_id":"task-123"}',
            delivery_count=5,
        )

    @pytest.mark.asyncio
    async def test_ack_event_uses_configured_stream_and_group(self):
        from dalston.common.durable_events import ack_event

        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()

        await ack_event(
            mock_redis,
            "1234567890-0",
            stream="custom-stream",
            group="custom-group",
        )

        mock_redis.xack.assert_called_once_with(
            "custom-stream",
            "custom-group",
            "1234567890-0",
        )

    @pytest.mark.asyncio
    async def test_move_to_dlq_writes_then_acks(self, envelope):
        from dalston.common.durable_events import move_event_to_dlq

        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="9876543210-0")
        mock_redis.xack = AsyncMock(return_value=1)

        await move_event_to_dlq(
            mock_redis,
            envelope,
            failure_reason="handler_exception",
            error="boom",
            consumer_id="orchestrator-1",
            dlq_stream="dalston:events:dlq",
            dlq_maxlen=1000,
        )

        call_names = [call_obj[0] for call_obj in mock_redis.method_calls]
        assert call_names[:2] == ["xadd", "xack"]

        xadd_fields = mock_redis.xadd.call_args[0][1]
        assert xadd_fields["source_message_id"] == "1234567890-0"
        assert xadd_fields["failure_reason"] == "handler_exception"
        assert xadd_fields["delivery_count"] == "5"
        assert "payload" in xadd_fields
        assert "raw_fields" in xadd_fields


class TestGetStreamInfo:
    @pytest.mark.asyncio
    async def test_gets_stream_and_dlq_info(self):
        from dalston.common.durable_events import get_stream_info

        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=[100, 7])
        mock_redis.xpending = AsyncMock(
            return_value={
                "pending": 5,
                "consumers": [{"name": "orch-1", "pending": 5}],
            }
        )

        info = await get_stream_info(mock_redis)

        assert info["stream_length"] == 100
        assert info["dlq_stream_length"] == 7
        assert info["pending_count"] == 5
        assert info["consumers"] == [{"name": "orch-1", "pending": 5}]


class TestPublishEventDurability:
    @pytest.mark.asyncio
    async def test_critical_event_writes_to_stream(self):
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234567890-0")

        await publish_event(
            mock_redis,
            "job.created",
            {"job_id": "test-123"},
        )

        mock_redis.publish.assert_called_once()
        mock_redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_critical_event_skips_stream(self):
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock()

        await publish_event(
            mock_redis,
            "some.other.event",
            {"data": "test"},
        )

        mock_redis.publish.assert_called_once()
        mock_redis.xadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_durable_override_true(self):
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234567890-0")

        await publish_event(
            mock_redis,
            "custom.event",
            {"data": "test"},
            durable=True,
        )

        mock_redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_durable_override_false(self):
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock()

        await publish_event(
            mock_redis,
            "job.created",
            {"job_id": "test"},
            durable=False,
        )

        mock_redis.xadd.assert_not_called()
