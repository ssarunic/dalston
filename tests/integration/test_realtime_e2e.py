"""End-to-end tests for realtime transcription WebSocket endpoint.

Tests the WebSocket endpoint behavior including connection handling,
session allocation, and management API endpoints.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.realtime_status import router as status_router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.session_router import CapacityInfo, SessionRouter, WorkerStatus


class TestRealtimeManagementEndpoints:
    """Tests for /v1/realtime/* management endpoints."""

    @pytest.fixture
    def mock_session_router(self):
        router = AsyncMock(spec=SessionRouter)
        return router

    @pytest.fixture
    def mock_api_key(self):
        """Create a mock API key with jobs:read scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.REALTIME],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_session_router, mock_api_key):
        from dalston.gateway.dependencies import get_session_router, require_auth

        app = FastAPI()
        # Note: status_router already has prefix="/realtime", so mount under /v1
        app.include_router(status_router, prefix="/v1")

        # Override dependencies
        app.dependency_overrides[get_session_router] = lambda: mock_session_router
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_get_realtime_status_ready(self, client, mock_session_router):
        mock_session_router.get_capacity.return_value = CapacityInfo(
            total_capacity=8,
            used_capacity=3,
            available_capacity=5,
            worker_count=2,
            ready_workers=2,
        )

        response = client.get("/v1/realtime/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["total_capacity"] == 8
        assert data["active_sessions"] == 3
        assert data["available_capacity"] == 5
        assert data["worker_count"] == 2
        assert data["ready_workers"] == 2

    def test_get_realtime_status_at_capacity(self, client, mock_session_router):
        mock_session_router.get_capacity.return_value = CapacityInfo(
            total_capacity=8,
            used_capacity=8,
            available_capacity=0,
            worker_count=2,
            ready_workers=2,
        )

        response = client.get("/v1/realtime/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "at_capacity"

    def test_get_realtime_status_unavailable(self, client, mock_session_router):
        mock_session_router.get_capacity.return_value = CapacityInfo(
            total_capacity=0,
            used_capacity=0,
            available_capacity=0,
            worker_count=0,
            ready_workers=0,
        )

        response = client.get("/v1/realtime/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unavailable"

    def test_list_realtime_workers(self, client, mock_session_router):
        mock_session_router.list_workers.return_value = [
            WorkerStatus(
                worker_id="worker-1",
                endpoint="ws://localhost:9000",
                status="ready",
                capacity=4,
                active_sessions=2,
                models=["fast", "accurate"],
                languages=["auto"],
            ),
            WorkerStatus(
                worker_id="worker-2",
                endpoint="ws://localhost:9001",
                status="busy",
                capacity=4,
                active_sessions=4,
                models=["fast"],
                languages=["auto"],
            ),
        ]

        response = client.get("/v1/realtime/workers")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["workers"]) == 2
        assert data["workers"][0]["worker_id"] == "worker-1"
        assert data["workers"][0]["status"] == "ready"
        assert data["workers"][1]["worker_id"] == "worker-2"
        assert data["workers"][1]["status"] == "busy"

    def test_get_worker_status(self, client, mock_session_router):
        mock_session_router.get_worker.return_value = WorkerStatus(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=2,
            models=["fast", "accurate"],
            languages=["auto"],
        )

        response = client.get("/v1/realtime/workers/worker-1")

        assert response.status_code == 200
        data = response.json()
        assert data["worker_id"] == "worker-1"
        assert data["endpoint"] == "ws://localhost:9000"
        assert data["status"] == "ready"
        assert data["capacity"] == 4
        assert data["active_sessions"] == 2

    def test_get_worker_status_not_found(self, client, mock_session_router):
        mock_session_router.get_worker.return_value = None

        response = client.get("/v1/realtime/workers/nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Worker not found"


class TestRealtimeProtocolMessages:
    """Tests for protocol message round-trip through JSON."""

    def test_session_begin_json_format(self):
        from dalston.realtime_sdk.protocol import (
            SessionBeginMessage,
            SessionConfigInfo,
        )

        config = SessionConfigInfo(
            sample_rate=16000,
            encoding="pcm_s16le",
            channels=1,
            language="en",
            model="fast",
        )
        msg = SessionBeginMessage(session_id="sess_abc123", config=config)

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "session.begin"
        assert parsed["session_id"] == "sess_abc123"
        assert parsed["config"]["sample_rate"] == 16000
        assert parsed["config"]["encoding"] == "pcm_s16le"

    def test_transcript_final_json_format(self):
        from dalston.realtime_sdk.protocol import (
            TranscriptFinalMessage,
            WordInfo,
        )

        words = [
            WordInfo(word="Hello", start=0.0, end=0.5, confidence=0.95),
            WordInfo(word="world", start=0.6, end=1.0, confidence=0.90),
        ]
        msg = TranscriptFinalMessage(
            text="Hello world",
            start=0.0,
            end=1.5,
            confidence=0.92,
            words=words,
        )

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "transcript.final"
        assert parsed["text"] == "Hello world"
        assert len(parsed["words"]) == 2
        assert parsed["words"][0]["word"] == "Hello"

    def test_error_message_json_format(self):
        from dalston.realtime_sdk.protocol import ErrorCode, ErrorMessage

        msg = ErrorMessage(
            code=ErrorCode.NO_CAPACITY,
            message="No workers available",
            recoverable=False,
        )

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "error"
        assert parsed["code"] == "no_capacity"
        assert parsed["message"] == "No workers available"
        assert parsed["recoverable"] is False

    def test_session_end_json_format(self):
        from dalston.realtime_sdk.protocol import (
            SegmentInfo,
            SessionEndMessage,
        )

        segments = [
            SegmentInfo(start=0.0, end=2.0, text="Hello"),
            SegmentInfo(start=3.0, end=5.0, text="World"),
        ]
        msg = SessionEndMessage(
            session_id="sess_abc",
            total_audio_seconds=10.0,
            total_speech_duration=4.0,
            transcript="Hello World",
            segments=segments,
        )

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "session.end"
        assert parsed["total_audio_seconds"] == 10.0
        assert parsed["total_speech_duration"] == 4.0
        assert len(parsed["segments"]) == 2


class TestTranscriptAssemblyE2E:
    """End-to-end tests for transcript assembly flow."""

    def test_multi_utterance_assembly(self):
        from dalston.realtime_sdk.assembler import (
            TranscribeResult,
            TranscriptAssembler,
            Word,
        )

        assembler = TranscriptAssembler()

        # Simulate receiving multiple utterances from VAD
        results = [
            TranscribeResult(
                text="Hello everyone",
                words=[
                    Word(word="Hello", start=0.0, end=0.5, confidence=0.95),
                    Word(word="everyone", start=0.6, end=1.2, confidence=0.92),
                ],
                language="en",
                confidence=0.93,
            ),
            TranscribeResult(
                text="Welcome to the show",
                words=[
                    Word(word="Welcome", start=0.0, end=0.5, confidence=0.90),
                    Word(word="to", start=0.6, end=0.7, confidence=0.95),
                    Word(word="the", start=0.8, end=0.9, confidence=0.94),
                    Word(word="show", start=1.0, end=1.3, confidence=0.91),
                ],
                language="en",
                confidence=0.92,
            ),
            TranscribeResult(
                text="Today we discuss",
                words=[
                    Word(word="Today", start=0.0, end=0.4, confidence=0.88),
                    Word(word="we", start=0.5, end=0.6, confidence=0.90),
                    Word(word="discuss", start=0.7, end=1.1, confidence=0.87),
                ],
                language="en",
                confidence=0.88,
            ),
        ]

        durations = [2.0, 2.0, 1.5]

        for result, duration in zip(results, durations, strict=False):
            assembler.add_utterance(result, audio_duration=duration)

        # Verify full transcript
        assert assembler.get_full_transcript() == (
            "Hello everyone Welcome to the show Today we discuss"
        )

        # Verify segment count
        assert assembler.segment_count == 3

        # Verify timeline
        segments = assembler.get_segments()
        assert segments[0].start == 0.0
        assert segments[0].end == 2.0
        assert segments[1].start == 2.0
        assert segments[1].end == 4.0
        assert segments[2].start == 4.0
        assert segments[2].end == 5.5

        # Verify word timestamps adjusted
        assert segments[1].words[0].word == "Welcome"
        assert segments[1].words[0].start == 2.0  # Adjusted from 0.0
        assert segments[2].words[0].word == "Today"
        assert segments[2].words[0].start == 4.0  # Adjusted from 0.0


class TestCapacityInfo:
    """Tests for CapacityInfo dataclass."""

    def test_capacity_info_creation(self):
        info = CapacityInfo(
            total_capacity=16,
            used_capacity=7,
            available_capacity=9,
            worker_count=4,
            ready_workers=3,
        )

        assert info.total_capacity == 16
        assert info.used_capacity == 7
        assert info.available_capacity == 9
        assert info.worker_count == 4
        assert info.ready_workers == 3


class TestWorkerStatus:
    """Tests for WorkerStatus dataclass."""

    def test_worker_status_creation(self):
        status = WorkerStatus(
            worker_id="realtime-whisper-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=2,
            models=["fast", "accurate"],
            languages=["auto", "en", "es"],
        )

        assert status.worker_id == "realtime-whisper-1"
        assert status.endpoint == "ws://localhost:9000"
        assert status.status == "ready"
        assert status.capacity == 4
        assert status.active_sessions == 2
        assert "fast" in status.models
        assert "auto" in status.languages
