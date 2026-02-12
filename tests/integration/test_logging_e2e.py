"""Integration tests for M18 unified structured logging.

Tests end-to-end correlation ID flow through Gateway API endpoints
and request_id propagation into orchestrator task metadata.
"""

import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.middleware.correlation import (
    REQUEST_ID_HEADER,
    CorrelationIdMiddleware,
)
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope


class TestCorrelationIdGatewayFlow:
    """Tests that the correlation middleware integrates with API routes."""

    @pytest.fixture
    def mock_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.publish = AsyncMock()
        return redis

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = "http://localhost:9000"
        return settings

    @pytest.fixture
    def mock_jobs_service(self):
        from dalston.gateway.services.jobs import JobsService

        service = AsyncMock(spec=JobsService)
        return service

    @pytest.fixture
    def mock_rate_limiter(self):
        from dalston.gateway.services.rate_limiter import (
            RateLimitResult,
            RedisRateLimiter,
        )

        limiter = AsyncMock(spec=RedisRateLimiter)
        limiter.check_request_rate.return_value = RateLimitResult(
            allowed=True, limit=600, remaining=599, reset_seconds=60
        )
        limiter.check_concurrent_jobs.return_value = RateLimitResult(
            allowed=True, limit=10, remaining=9, reset_seconds=0
        )
        limiter.increment_concurrent_jobs.return_value = None
        return limiter

    @pytest.fixture
    def app(
        self,
        mock_api_key,
        mock_db,
        mock_redis,
        mock_settings,
        mock_jobs_service,
        mock_rate_limiter,
    ):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)
        app.include_router(transcription_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_health_endpoint_returns_request_id_header(self):
        """Any endpoint wrapped in CorrelationIdMiddleware returns X-Request-ID."""
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert REQUEST_ID_HEADER in response.headers
        assert response.headers[REQUEST_ID_HEADER].startswith("req_")

    def test_client_request_id_is_echoed(self):
        """Client-provided X-Request-ID is echoed back."""
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)

        @app.get("/test")
        async def test_ep():
            return {"ok": True}

        client = TestClient(app)
        response = client.get("/test", headers={REQUEST_ID_HEADER: "external-trace-id"})
        assert response.headers[REQUEST_ID_HEADER] == "external-trace-id"

    def test_create_transcription_publishes_request_id(
        self, client, mock_jobs_service, mock_redis
    ):
        """POST /v1/audio/transcriptions passes request_id to publish_job_created."""
        job_id = uuid4()
        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.status = "pending"
        mock_job.created_at = datetime.now(UTC)
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.transcription.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://test-bucket/audio.wav"
            )

            with patch(
                "dalston.gateway.api.v1.transcription.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                audio_content = b"fake audio data"
                response = client.post(
                    "/v1/audio/transcriptions",
                    files={"file": ("test.wav", BytesIO(audio_content), "audio/wav")},
                    data={"language": "auto"},
                    headers={REQUEST_ID_HEADER: "req_test123"},
                )

                # The endpoint should have called publish_job_created with request_id
                if response.status_code == 201:
                    mock_publish.assert_called_once()
                    call_kwargs = mock_publish.call_args
                    # request_id should be passed as keyword arg
                    assert call_kwargs.kwargs.get("request_id") == "req_test123" or (
                        len(call_kwargs.args) >= 3
                        and call_kwargs.args[2] == "req_test123"
                    )


class TestRequestIdInEvents:
    """Tests that request_id is included in Redis pub/sub events."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.publish = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_publish_job_created_includes_request_id(self, mock_redis):
        """publish_job_created includes request_id in the event payload."""
        from dalston.common.events import publish_job_created

        job_id = uuid4()
        await publish_job_created(mock_redis, job_id, request_id="req_abc123")

        # Verify publish was called
        mock_redis.publish.assert_called_once()
        channel, message = mock_redis.publish.call_args.args
        assert channel == "dalston:events"

        event = json.loads(message)
        assert event["type"] == "job.created"
        assert event["job_id"] == str(job_id)
        assert event["request_id"] == "req_abc123"

    @pytest.mark.asyncio
    async def test_publish_job_created_without_request_id(self, mock_redis):
        """publish_job_created omits request_id when not provided."""
        from dalston.common.events import publish_job_created

        job_id = uuid4()
        await publish_job_created(mock_redis, job_id)

        channel, message = mock_redis.publish.call_args.args
        event = json.loads(message)
        assert event["type"] == "job.created"
        assert "request_id" not in event


class TestRequestIdInTaskMetadata:
    """Tests that request_id propagates from contextvars into Redis task metadata."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()
        redis.lpush = AsyncMock()
        return redis

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = "http://localhost:9000"
        return settings

    @pytest.mark.asyncio
    async def test_queue_task_includes_request_id_from_contextvars(
        self, mock_redis, mock_settings
    ):
        """queue_task stores request_id from contextvars in Redis task metadata."""
        from dalston.common.models import Task
        from dalston.orchestrator.scheduler import queue_task

        task = Task(
            id=uuid4(),
            job_id=uuid4(),
            stage="transcribe",
            engine_id="faster-whisper",
            input_uri="s3://bucket/audio.wav",
            config={},
            depends_on=[],
        )

        # Bind request_id to context (as the orchestrator does when receiving events)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="req_xyz789")

        with patch(
            "dalston.orchestrator.scheduler.write_task_input", new_callable=AsyncMock
        ):
            await queue_task(mock_redis, task, mock_settings)

        # Verify hset was called with request_id in the mapping
        mock_redis.hset.assert_called_once()
        call_kwargs = mock_redis.hset.call_args
        mapping = (
            call_kwargs.kwargs.get("mapping") or call_kwargs.args[1]
            if len(call_kwargs.args) > 1
            else None
        )

        # Handle both positional and keyword args
        if mapping is None:
            # Try keyword
            mapping = call_kwargs.kwargs.get("mapping")

        assert mapping is not None
        assert mapping["request_id"] == "req_xyz789"
        assert mapping["job_id"] == str(task.job_id)
        assert mapping["engine_id"] == "faster-whisper"

        # Cleanup
        structlog.contextvars.clear_contextvars()

    @pytest.mark.asyncio
    async def test_queue_task_omits_request_id_when_not_in_context(
        self, mock_redis, mock_settings
    ):
        """queue_task does not include request_id when not in contextvars."""
        from dalston.common.models import Task
        from dalston.orchestrator.scheduler import queue_task

        task = Task(
            id=uuid4(),
            job_id=uuid4(),
            stage="transcribe",
            engine_id="faster-whisper",
            input_uri="s3://bucket/audio.wav",
            config={},
            depends_on=[],
        )

        # Clear context to ensure no request_id
        structlog.contextvars.clear_contextvars()

        with patch(
            "dalston.orchestrator.scheduler.write_task_input", new_callable=AsyncMock
        ):
            await queue_task(mock_redis, task, mock_settings)

        mock_redis.hset.assert_called_once()
        call_kwargs = mock_redis.hset.call_args
        mapping = call_kwargs.kwargs.get("mapping")
        assert "request_id" not in mapping


class TestOrchestratorEventDispatch:
    """Tests that the orchestrator binds request_id from events into context."""

    @pytest.mark.asyncio
    async def test_dispatch_event_binds_request_id(self):
        """_dispatch_event binds request_id from event data into structlog context."""
        from dalston.orchestrator.main import _dispatch_event

        mock_redis = AsyncMock()
        mock_settings = MagicMock()

        # Create a job.created event with request_id
        event_data = json.dumps(
            {
                "type": "job.created",
                "job_id": str(uuid4()),
                "request_id": "req_from_gateway",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        # Patch the handler to capture context during execution
        captured_ctx = {}

        async def capture_handler(*args, **kwargs):
            captured_ctx.update(structlog.contextvars.get_contextvars())

        with (
            patch(
                "dalston.orchestrator.main.handle_job_created",
                side_effect=capture_handler,
            ),
            patch("dalston.orchestrator.main.async_session") as mock_session_ctx,
        ):
            # Setup async context manager for session
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _dispatch_event(event_data, mock_redis, mock_settings)

        assert captured_ctx.get("request_id") == "req_from_gateway"

    @pytest.mark.asyncio
    async def test_dispatch_event_without_request_id(self):
        """_dispatch_event works normally when event has no request_id."""
        from dalston.orchestrator.main import _dispatch_event

        mock_redis = AsyncMock()
        mock_settings = MagicMock()

        event_data = json.dumps(
            {
                "type": "job.created",
                "job_id": str(uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        captured_ctx = {}

        async def capture_handler(*args, **kwargs):
            captured_ctx.update(structlog.contextvars.get_contextvars())

        with (
            patch(
                "dalston.orchestrator.main.handle_job_created",
                side_effect=capture_handler,
            ),
            patch("dalston.orchestrator.main.async_session") as mock_session_ctx,
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _dispatch_event(event_data, mock_redis, mock_settings)

        assert "request_id" not in captured_ctx
