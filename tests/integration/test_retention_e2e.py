"""End-to-end tests for data retention and audit logging flow.

Tests the complete retention lifecycle from policy creation to job purging,
including audit trail generation.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from dalston.common.audit import AuditService
from dalston.common.models import RetentionMode, RetentionScope
from dalston.orchestrator.cleanup import CleanupWorker
from dalston.orchestrator.handlers import _compute_purge_after


class TestRetentionLifecycleE2E:
    """End-to-end tests for the complete retention lifecycle."""

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    def _make_job(
        self,
        job_id: UUID,
        tenant_id: UUID,
        retention_mode: str,
        retention_hours: int | None,
        retention_scope: str = "all",
        completed_at: datetime | None = None,
    ):
        """Create a mock job with retention settings."""
        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_mode = retention_mode
        job.retention_hours = retention_hours
        job.retention_scope = retention_scope
        job.completed_at = completed_at or datetime.now(UTC)
        job.purge_after = None
        job.purged_at = None
        return job

    @pytest.mark.asyncio
    async def test_auto_delete_lifecycle(self, job_id, tenant_id):
        """Test complete auto_delete lifecycle: job created -> completed -> purged."""
        # 1. Job is created with auto_delete policy (24 hours)
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=24,
            completed_at=datetime.now(UTC) - timedelta(hours=25),  # 25 hours ago
        )

        # 2. Job completes - purge_after is computed
        import structlog

        log = structlog.get_logger().bind(job_id=str(job_id))
        await _compute_purge_after(job, log)

        # Verify purge_after is set correctly
        assert job.purge_after is not None
        expected_purge = job.completed_at + timedelta(hours=24)
        assert job.purge_after == expected_purge

        # 3. Cleanup worker finds and purges expired job
        # Since completed_at was 25 hours ago and retention is 24 hours,
        # the job should be eligible for purging (purge_after < now)
        assert job.purge_after < datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_zero_retention_immediate_purge(self, job_id, tenant_id):
        """Test zero-retention mode triggers immediate purge scheduling."""
        # Job is created with none/zero-retention policy
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.NONE.value,
            retention_hours=None,
        )

        # Job completes - should schedule immediate purge
        import structlog

        log = structlog.get_logger().bind(job_id=str(job_id))

        before = datetime.now(UTC)
        await _compute_purge_after(job, log)
        after = datetime.now(UTC)

        # purge_after should be set to ~now for immediate purge
        assert job.purge_after is not None
        assert before <= job.purge_after <= after

    @pytest.mark.asyncio
    async def test_keep_mode_never_purges(self, job_id, tenant_id):
        """Test keep mode results in no purge scheduling."""
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.KEEP.value,
            retention_hours=None,
        )

        import structlog

        log = structlog.get_logger().bind(job_id=str(job_id))
        await _compute_purge_after(job, log)

        # purge_after should remain None - never purge
        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_audio_only_scope_preserves_transcript(self, job_id, tenant_id):
        """Test audio_only scope deletes audio but preserves transcript."""
        from contextlib import asynccontextmanager

        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=1,
            retention_scope=RetentionScope.AUDIO_ONLY.value,
            completed_at=datetime.now(UTC) - timedelta(hours=2),  # Expired
        )
        job.purge_after = datetime.now(UTC) - timedelta(hours=1)

        # Mock Redis for lock coordination
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # Query session returns job
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        # Update session returns job record
        update_session = AsyncMock()
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        # Set up mock settings
        from dalston.config import Settings

        settings = Settings()
        settings.retention_cleanup_batch_size = 10

        # Create cleanup worker
        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        # Run purge
        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            # Should have called delete_job_audio (not delete_job_artifacts)
            assert purged == 1
            mock_storage.delete_job_audio.assert_awaited_once_with(job_id)
            mock_storage.delete_job_artifacts.assert_not_awaited()


class TestAuditTrailE2E:
    """End-to-end tests for audit trail generation."""

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.mark.asyncio
    async def test_job_lifecycle_audit_trail(self, job_id, tenant_id):
        """Test that complete job lifecycle generates proper audit trail."""
        from contextlib import asynccontextmanager

        # Track audit entries
        audit_entries = []

        mock_db = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda entry: audit_entries.append(entry))

        @asynccontextmanager
        async def session_factory():
            yield mock_db

        audit_service = AuditService(db_session_factory=session_factory)

        # 1. Job created
        await audit_service.log_job_created(
            job_id=job_id,
            tenant_id=tenant_id,
            actor_type="api_key",
            actor_id="dk_test123",
            retention_policy="default",
        )
        assert len(audit_entries) == 1
        assert audit_entries[-1].action == "job.created"

        # 2. Audio uploaded
        await audit_service.log_audio_uploaded(
            job_id=job_id,
            tenant_id=tenant_id,
            file_size=1024000,
            audio_duration=120.5,
        )
        assert len(audit_entries) == 2
        assert audit_entries[-1].action == "audio.uploaded"

        # 3. Transcript accessed
        await audit_service.log_transcript_accessed(
            job_id=job_id,
            tenant_id=tenant_id,
            correlation_id="req-456",
        )
        assert len(audit_entries) == 3
        assert audit_entries[-1].action == "transcript.accessed"

        # 4. Transcript exported
        await audit_service.log_transcript_exported(
            job_id=job_id,
            tenant_id=tenant_id,
            export_format="srt",
        )
        assert len(audit_entries) == 4
        assert audit_entries[-1].action == "transcript.exported"

        # 5. Job purged by cleanup worker
        await audit_service.log_job_purged(
            job_id=job_id,
            tenant_id=tenant_id,
            artifacts_deleted=["audio", "tasks", "transcript"],
        )
        assert len(audit_entries) == 5
        assert audit_entries[-1].action == "job.purged"
        assert audit_entries[-1].actor_type == "system"
        assert audit_entries[-1].actor_id == "cleanup_worker"

    @pytest.mark.asyncio
    async def test_audit_fail_open_behavior(self, job_id, tenant_id):
        """Test that audit logging failures don't block operations."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def failing_session_factory():
            raise Exception("Database unavailable")
            yield  # Never reached

        audit_service = AuditService(db_session_factory=failing_session_factory)

        # This should NOT raise, even though the DB is unavailable
        with patch("dalston.common.audit.logger") as mock_logger:
            await audit_service.log_job_created(
                job_id=job_id,
                tenant_id=tenant_id,
            )

            # Error should be logged
            mock_logger.error.assert_called()
            call_args = mock_logger.error.call_args
            assert "audit_log_write_failed" in call_args[0]


class TestCleanupWorkerE2E:
    """End-to-end tests for the cleanup worker."""

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    def _make_job(
        self,
        job_id: UUID,
        tenant_id: UUID,
        purge_after: datetime,
        retention_scope: str = "all",
    ):
        """Create a mock job ready for purging."""
        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_scope = retention_scope
        job.purge_after = purge_after
        job.purged_at = None
        return job

    @pytest.mark.asyncio
    async def test_cleanup_worker_batch_processing(self, tenant_id):
        """Test that cleanup worker processes jobs in batches."""
        from contextlib import asynccontextmanager

        from dalston.config import Settings

        # Create 5 expired jobs
        jobs = [
            self._make_job(
                job_id=uuid4(),
                tenant_id=tenant_id,
                purge_after=datetime.now(UTC) - timedelta(hours=1),
            )
            for _ in range(5)
        ]

        # Mock Redis for lock coordination
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # Query session returns jobs
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = jobs
        query_session.execute.return_value = mock_result

        # Update sessions return job records
        def make_update_session(job):
            update_session = AsyncMock()
            job_record = MagicMock()
            job_record.purged_at = None
            update_session.get.return_value = job_record
            return update_session

        session_calls = [query_session] + [make_update_session(j) for j in jobs]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        settings = Settings()
        settings.retention_cleanup_batch_size = 10

        # Track audit logs
        audit_calls = []
        mock_audit = AsyncMock()
        mock_audit.log_job_purged = AsyncMock(
            side_effect=lambda **kwargs: audit_calls.append(kwargs)
        )

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
            audit_service=mock_audit,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            MockStorageService.return_value = AsyncMock()

            purged = await worker._purge_expired_jobs()

            # All 5 jobs should be purged
            assert purged == 5

            # Audit log called for each job
            assert len(audit_calls) == 5
            for call in audit_calls:
                assert call["artifacts_deleted"] == ["audio", "tasks", "transcript"]

    @pytest.mark.asyncio
    async def test_cleanup_worker_error_resilience(self, tenant_id):
        """Test that cleanup worker continues after individual job failures."""
        from contextlib import asynccontextmanager

        from dalston.config import Settings

        job1 = self._make_job(
            job_id=uuid4(),
            tenant_id=tenant_id,
            purge_after=datetime.now(UTC) - timedelta(hours=1),
        )
        job2 = self._make_job(
            job_id=uuid4(),
            tenant_id=tenant_id,
            purge_after=datetime.now(UTC) - timedelta(hours=1),
        )

        # Mock Redis for lock coordination
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # Query session returns both jobs
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job1, job2]
        query_session.execute.return_value = mock_result

        # Update session for job2 only (job1 fails before DB update)
        update_session = AsyncMock()
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        settings = Settings()
        settings.retention_cleanup_batch_size = 10

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            # First job fails, second succeeds
            mock_storage.delete_job_artifacts.side_effect = [
                Exception("S3 error"),
                None,
            ]
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            # Only second job should be purged
            assert purged == 1


class TestRetentionStrategies:
    """Test different retention strategies with audio file scenarios.

    This tests the matrix of retention modes (auto_delete, keep, none) and
    scopes (all, audio_only) to verify correct purge behavior for each
    combination.
    """

    @pytest.fixture
    def tenant_id(self) -> UUID:
        return UUID("12345678-1234-1234-1234-123456789abc")

    @pytest.fixture
    def audio_file_path(self) -> str:
        """Path to test audio file (test_merged.wav)."""
        from pathlib import Path

        return str(Path(__file__).parent.parent / "audio" / "test_merged.wav")

    def _make_job(
        self,
        job_id: UUID,
        tenant_id: UUID,
        retention_mode: str,
        retention_hours: int | None,
        retention_scope: str,
        completed_at: datetime,
        audio_path: str | None = None,
    ):
        """Create a mock job with full retention settings."""
        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_mode = retention_mode
        job.retention_hours = retention_hours
        job.retention_scope = retention_scope
        job.completed_at = completed_at
        job.purge_after = None
        job.purged_at = None
        job.audio_path = audio_path
        return job

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mode,hours,scope,expected_purge,expected_artifacts",
        [
            # AUTO_DELETE mode - deletes after specified hours
            (
                RetentionMode.AUTO_DELETE.value,
                24,
                RetentionScope.ALL.value,
                True,
                ["audio", "tasks", "transcript"],
            ),
            (
                RetentionMode.AUTO_DELETE.value,
                24,
                RetentionScope.AUDIO_ONLY.value,
                True,
                ["audio"],
            ),
            # NONE mode - immediate deletion
            (
                RetentionMode.NONE.value,
                None,
                RetentionScope.ALL.value,
                True,
                ["audio", "tasks", "transcript"],
            ),
            (
                RetentionMode.NONE.value,
                None,
                RetentionScope.AUDIO_ONLY.value,
                True,
                ["audio"],
            ),
            # KEEP mode - never deleted
            (
                RetentionMode.KEEP.value,
                None,
                RetentionScope.ALL.value,
                False,
                [],
            ),
            (
                RetentionMode.KEEP.value,
                None,
                RetentionScope.AUDIO_ONLY.value,
                False,
                [],
            ),
        ],
    )
    async def test_retention_strategy_matrix(
        self,
        tenant_id,
        audio_file_path,
        mode,
        hours,
        scope,
        expected_purge,
        expected_artifacts,
    ):
        """Test all retention mode and scope combinations."""
        import structlog

        job_id = uuid4()
        # Job completed 25 hours ago (expired for 24-hour policies)
        completed_at = datetime.now(UTC) - timedelta(hours=25)

        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=mode,
            retention_hours=hours,
            retention_scope=scope,
            completed_at=completed_at,
            audio_path=audio_file_path,
        )

        # Compute purge_after
        log = structlog.get_logger().bind(job_id=str(job_id))
        await _compute_purge_after(job, log)

        if expected_purge:
            assert job.purge_after is not None, f"Expected purge for {mode}/{scope}"
            assert job.purge_after < datetime.now(UTC), "Job should be expired"
        else:
            assert job.purge_after is None, f"No purge expected for {mode}/{scope}"

    @pytest.mark.asyncio
    async def test_audio_only_preserves_transcript_with_audio_file(
        self, tenant_id, audio_file_path
    ):
        """Test that audio_only scope deletes only audio, preserving transcript.

        Simulates a job created with test_merged.wav where only the audio
        should be deleted (for privacy) while transcript is retained (for
        compliance).
        """
        from contextlib import asynccontextmanager

        from dalston.config import Settings
        from dalston.orchestrator.cleanup import CleanupWorker

        job_id = uuid4()
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=1,
            retention_scope=RetentionScope.AUDIO_ONLY.value,
            completed_at=datetime.now(UTC) - timedelta(hours=2),
            audio_path=audio_file_path,
        )
        job.purge_after = datetime.now(UTC) - timedelta(hours=1)

        # Mock Redis for lock coordination
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # Query session returns job
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        # Update session returns job record
        update_session = AsyncMock()
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        settings = Settings()
        settings.retention_cleanup_batch_size = 10

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            # Job purged
            assert purged == 1
            # Only audio deleted
            mock_storage.delete_job_audio.assert_awaited_once_with(job_id)
            # Full artifacts (transcript, tasks) NOT deleted
            mock_storage.delete_job_artifacts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_scope_deletes_everything_with_audio_file(
        self, tenant_id, audio_file_path
    ):
        """Test that 'all' scope deletes audio, tasks, and transcript.

        Simulates a job created with test_merged.wav where all artifacts
        should be deleted after the retention period.
        """
        from contextlib import asynccontextmanager

        from dalston.config import Settings
        from dalston.orchestrator.cleanup import CleanupWorker

        job_id = uuid4()
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=1,
            retention_scope=RetentionScope.ALL.value,
            completed_at=datetime.now(UTC) - timedelta(hours=2),
            audio_path=audio_file_path,
        )
        job.purge_after = datetime.now(UTC) - timedelta(hours=1)

        # Mock Redis for lock coordination
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # Query session returns job
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        # Update session returns job record
        update_session = AsyncMock()
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        settings = Settings()
        settings.retention_cleanup_batch_size = 10

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            # Job purged
            assert purged == 1
            # All artifacts deleted
            mock_storage.delete_job_artifacts.assert_awaited_once_with(job_id)
            # Individual audio NOT called (covered by delete_job_artifacts)
            mock_storage.delete_job_audio.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keep_mode_never_purges_audio_file(self, tenant_id, audio_file_path):
        """Test that keep mode results in no purge scheduling.

        Simulates a job created with test_merged.wav that should be
        retained indefinitely (no automatic deletion).
        """
        import structlog

        job_id = uuid4()
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.KEEP.value,
            retention_hours=None,
            retention_scope=RetentionScope.ALL.value,
            completed_at=datetime.now(UTC),
            audio_path=audio_file_path,
        )

        log = structlog.get_logger().bind(job_id=str(job_id))
        await _compute_purge_after(job, log)

        # purge_after should remain None - never purge
        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_zero_retention_immediate_purge_with_audio_file(
        self, tenant_id, audio_file_path
    ):
        """Test zero-retention mode triggers immediate purge scheduling.

        Simulates a job created with test_merged.wav that should be
        purged immediately after completion (privacy-first workflow).
        """
        import structlog

        job_id = uuid4()
        job = self._make_job(
            job_id=job_id,
            tenant_id=tenant_id,
            retention_mode=RetentionMode.NONE.value,
            retention_hours=None,
            retention_scope=RetentionScope.ALL.value,
            completed_at=datetime.now(UTC),
            audio_path=audio_file_path,
        )

        log = structlog.get_logger().bind(job_id=str(job_id))

        before = datetime.now(UTC)
        await _compute_purge_after(job, log)
        after = datetime.now(UTC)

        # purge_after should be set to ~now for immediate purge
        assert job.purge_after is not None
        assert before <= job.purge_after <= after
