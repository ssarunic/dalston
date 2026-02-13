"""Unit tests for AuditService with fail-open behavior."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from dalston.common.audit import AuditService


class TestAuditServiceLog:
    """Tests for AuditService.log method."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        return session

    @pytest.fixture
    def audit_service(self, mock_db_session):
        """Create an AuditService with mock db session factory."""

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return AuditService(db_session_factory=session_factory)

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_log_creates_audit_entry(
        self, audit_service: AuditService, mock_db_session, tenant_id
    ):
        """Test that log creates an audit entry."""
        await audit_service.log(
            action="job.created",
            resource_type="job",
            resource_id="test-job-id",
            tenant_id=tenant_id,
            actor_type="api_key",
            actor_id="sk_test",
            detail={"retention_policy": "default"},
            correlation_id="corr-123",
        )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_awaited_once()

        # Verify the audit entry was created with correct fields
        call_args = mock_db_session.add.call_args
        audit_entry = call_args[0][0]
        assert audit_entry.action == "job.created"
        assert audit_entry.resource_type == "job"
        assert audit_entry.resource_id == "test-job-id"
        assert audit_entry.tenant_id == tenant_id
        assert audit_entry.actor_type == "api_key"
        assert audit_entry.actor_id == "sk_test"
        assert audit_entry.detail == {"retention_policy": "default"}
        assert audit_entry.correlation_id == "corr-123"

    @pytest.mark.asyncio
    async def test_log_fail_open_on_db_error(self, tenant_id):
        """Test that log fails open when database error occurs."""
        mock_session = AsyncMock()
        mock_session.commit.side_effect = Exception("Database unavailable")

        @asynccontextmanager
        async def failing_session_factory():
            yield mock_session

        audit_service = AuditService(db_session_factory=failing_session_factory)

        # Should not raise, just log the error
        with patch("dalston.common.audit.logger") as mock_logger:
            await audit_service.log(
                action="job.created",
                resource_type="job",
                resource_id="test-job-id",
                tenant_id=tenant_id,
            )

            # Verify error was logged but exception was not raised
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args
            assert "audit_log_write_failed" in call_args[0]

    @pytest.mark.asyncio
    async def test_log_fail_open_on_session_error(self, tenant_id):
        """Test that log fails open when session creation fails."""

        @asynccontextmanager
        async def failing_session_factory():
            raise Exception("Cannot create session")
            yield  # This line is never reached

        audit_service = AuditService(db_session_factory=failing_session_factory)

        # Should not raise
        with patch("dalston.common.audit.logger") as mock_logger:
            await audit_service.log(
                action="job.created",
                resource_type="job",
                resource_id="test-job-id",
                tenant_id=tenant_id,
            )

            mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_with_optional_fields(
        self, audit_service: AuditService, mock_db_session
    ):
        """Test logging with optional fields."""
        await audit_service.log(
            action="transcript.accessed",
            resource_type="job",
            resource_id="job-123",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        call_args = mock_db_session.add.call_args
        audit_entry = call_args[0][0]
        assert audit_entry.ip_address == "192.168.1.1"
        assert audit_entry.user_agent == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_log_with_defaults(
        self, audit_service: AuditService, mock_db_session
    ):
        """Test logging uses correct default values."""
        await audit_service.log(
            action="test.action",
            resource_type="test",
            resource_id="test-id",
        )

        call_args = mock_db_session.add.call_args
        audit_entry = call_args[0][0]
        assert audit_entry.actor_type == "system"
        assert audit_entry.actor_id == "unknown"
        assert audit_entry.tenant_id is None
        assert audit_entry.detail is None


class TestAuditServiceJobMethods:
    """Tests for job-related audit convenience methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def audit_service(self, mock_db_session):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return AuditService(db_session_factory=session_factory)

    @pytest.fixture
    def job_id(self) -> UUID:
        return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_log_job_created(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_job_created method."""
        await audit_service.log_job_created(
            job_id=job_id,
            tenant_id=tenant_id,
            actor_type="api_key",
            actor_id="sk_test_123",
            retention_policy="short-term",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "job.created"
        assert entry.resource_type == "job"
        assert entry.resource_id == str(job_id)
        assert entry.detail == {"retention_policy": "short-term"}

    @pytest.mark.asyncio
    async def test_log_job_created_without_retention(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_job_created without retention policy."""
        await audit_service.log_job_created(
            job_id=job_id,
            tenant_id=tenant_id,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.detail is None

    @pytest.mark.asyncio
    async def test_log_audio_uploaded(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_audio_uploaded method."""
        await audit_service.log_audio_uploaded(
            job_id=job_id,
            tenant_id=tenant_id,
            file_size=1024000,
            audio_duration=120.5,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "audio.uploaded"
        assert entry.detail == {"file_size": 1024000, "audio_duration": 120.5}

    @pytest.mark.asyncio
    async def test_log_transcript_accessed(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_transcript_accessed method."""
        await audit_service.log_transcript_accessed(
            job_id=job_id,
            tenant_id=tenant_id,
            correlation_id="req-456",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "transcript.accessed"
        assert entry.correlation_id == "req-456"

    @pytest.mark.asyncio
    async def test_log_transcript_exported(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_transcript_exported method."""
        await audit_service.log_transcript_exported(
            job_id=job_id,
            tenant_id=tenant_id,
            export_format="srt",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "transcript.exported"
        assert entry.detail == {"format": "srt"}

    @pytest.mark.asyncio
    async def test_log_audio_deleted(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_audio_deleted method."""
        await audit_service.log_audio_deleted(
            job_id=job_id,
            tenant_id=tenant_id,
            ip_address="10.0.0.1",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "audio.deleted"
        assert entry.ip_address == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_log_job_deleted(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_job_deleted method."""
        await audit_service.log_job_deleted(
            job_id=job_id,
            tenant_id=tenant_id,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "job.deleted"

    @pytest.mark.asyncio
    async def test_log_job_purged(
        self, audit_service: AuditService, mock_db_session, job_id, tenant_id
    ):
        """Test log_job_purged method (automated cleanup)."""
        await audit_service.log_job_purged(
            job_id=job_id,
            tenant_id=tenant_id,
            artifacts_deleted=["audio", "tasks"],
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "job.purged"
        assert entry.actor_type == "system"
        assert entry.actor_id == "cleanup_worker"
        assert entry.detail == {"artifacts_deleted": ["audio", "tasks"]}


class TestAuditServiceSessionMethods:
    """Tests for session-related audit convenience methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def audit_service(self, mock_db_session):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return AuditService(db_session_factory=session_factory)

    @pytest.fixture
    def session_id(self) -> UUID:
        return UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_log_session_started(
        self, audit_service: AuditService, mock_db_session, session_id, tenant_id
    ):
        """Test log_session_started method."""
        await audit_service.log_session_started(
            session_id=session_id,
            tenant_id=tenant_id,
            worker_id="worker-1",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "session.started"
        assert entry.resource_type == "session"
        assert entry.resource_id == str(session_id)
        assert entry.detail == {"worker_id": "worker-1"}

    @pytest.mark.asyncio
    async def test_log_session_ended(
        self, audit_service: AuditService, mock_db_session, session_id, tenant_id
    ):
        """Test log_session_ended method."""
        await audit_service.log_session_ended(
            session_id=session_id,
            tenant_id=tenant_id,
            duration_seconds=300.5,
            word_count=1500,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "session.ended"
        assert entry.actor_type == "system"
        assert entry.actor_id == "session_router"
        assert entry.detail == {"duration_seconds": 300.5, "word_count": 1500}


class TestAuditServiceApiKeyMethods:
    """Tests for API key audit methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def audit_service(self, mock_db_session):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return AuditService(db_session_factory=session_factory)

    @pytest.fixture
    def key_id(self) -> UUID:
        return UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_log_api_key_created(
        self, audit_service: AuditService, mock_db_session, key_id, tenant_id
    ):
        """Test log_api_key_created method."""
        await audit_service.log_api_key_created(
            key_id=key_id,
            tenant_id=tenant_id,
            key_name="production-key",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "api_key.created"
        assert entry.resource_type == "api_key"
        assert entry.detail == {"key_name": "production-key"}

    @pytest.mark.asyncio
    async def test_log_api_key_revoked(
        self, audit_service: AuditService, mock_db_session, key_id, tenant_id
    ):
        """Test log_api_key_revoked method."""
        await audit_service.log_api_key_revoked(
            key_id=key_id,
            tenant_id=tenant_id,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "api_key.revoked"


class TestAuditServiceRetentionPolicyMethods:
    """Tests for retention policy audit methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def audit_service(self, mock_db_session):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return AuditService(db_session_factory=session_factory)

    @pytest.fixture
    def policy_id(self) -> UUID:
        return UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.mark.asyncio
    async def test_log_retention_policy_created(
        self, audit_service: AuditService, mock_db_session, policy_id, tenant_id
    ):
        """Test log_retention_policy_created method."""
        await audit_service.log_retention_policy_created(
            policy_id=policy_id,
            tenant_id=tenant_id,
            policy_name="short-term",
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "retention_policy.created"
        assert entry.resource_type == "retention_policy"
        assert entry.detail == {"policy_name": "short-term"}

    @pytest.mark.asyncio
    async def test_log_retention_policy_deleted(
        self, audit_service: AuditService, mock_db_session, policy_id, tenant_id
    ):
        """Test log_retention_policy_deleted method."""
        await audit_service.log_retention_policy_deleted(
            policy_id=policy_id,
            tenant_id=tenant_id,
        )

        call_args = mock_db_session.add.call_args
        entry = call_args[0][0]
        assert entry.action == "retention_policy.deleted"
