"""Service for persisting realtime sessions to PostgreSQL and S3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import RetentionMode
from dalston.common.utils import parse_session_id
from dalston.db.models import RealtimeSessionModel

if TYPE_CHECKING:
    from dalston.config import Settings

logger = structlog.get_logger()


class RealtimeSessionService:
    """Service for managing persistent realtime sessions.

    Handles:
    - Creating session records in PostgreSQL on session start
    - Updating session stats periodically
    - Finalizing sessions with status and artifacts on end
    - Querying session history
    """

    def __init__(self, db: AsyncSession, settings: Settings):
        self.db = db
        self.settings = settings

    async def create_session(
        self,
        session_id: str,
        tenant_id: UUID,
        worker_id: str,
        client_ip: str,
        language: str | None = None,
        model: str | None = None,
        engine: str | None = None,
        encoding: str | None = None,
        sample_rate: int | None = None,
        store_audio: bool = True,
        store_transcript: bool = True,
        enhance_on_end: bool = True,
        previous_session_id: UUID | None = None,
        # Retention fields (M25)
        retention_policy_id: UUID | None = None,
        retention_mode: str = "auto_delete",
        retention_hours: int | None = None,
    ) -> RealtimeSessionModel:
        """Create a new session record in PostgreSQL.

        Args:
            session_id: Session ID (from session router, e.g., sess_abc123)
            tenant_id: Tenant UUID
            worker_id: Assigned worker ID
            client_ip: Client IP address
            language: Language code or "auto"
            model: Model variant requested by user
            engine: Engine type that handled the session (e.g., "parakeet", "whisper")
            encoding: Audio encoding
            sample_rate: Audio sample rate
            store_audio: Whether to record audio to S3
            store_transcript: Whether to save transcript to S3
            enhance_on_end: Whether to trigger batch enhancement
            previous_session_id: Previous session for resume linking
            retention_policy_id: Reference to retention policy
            retention_mode: Snapshotted mode (auto_delete, keep, none)
            retention_hours: Snapshotted hours for auto_delete

        Returns:
            Created RealtimeSessionModel
        """
        # Parse session_id to UUID (strip sess_ prefix if present)
        session_uuid = self._parse_session_id(session_id)

        session = RealtimeSessionModel(
            id=session_uuid,
            tenant_id=tenant_id,
            status="active",
            language=language,
            model=model,
            engine=engine,
            encoding=encoding,
            sample_rate=sample_rate,
            store_audio=store_audio,
            store_transcript=store_transcript,
            enhance_on_end=enhance_on_end,
            worker_id=worker_id,
            client_ip=client_ip,
            previous_session_id=previous_session_id,
            started_at=datetime.now(UTC),
            # Retention fields
            retention_policy_id=retention_policy_id,
            retention_mode=retention_mode,
            retention_hours=retention_hours,
        )

        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)

        logger.info(
            "realtime_session_created",
            session_id=session_id,
            tenant_id=str(tenant_id),
            store_audio=store_audio,
            store_transcript=store_transcript,
        )

        return session

    async def update_stats(
        self,
        session_id: str,
        audio_duration_seconds: float | None = None,
        segment_count: int | None = None,
        word_count: int | None = None,
    ) -> None:
        """Update session statistics.

        Called periodically during the session.

        Args:
            session_id: Session ID
            audio_duration_seconds: Total audio duration
            segment_count: Number of segments
            word_count: Number of words
        """
        session_uuid = self._parse_session_id(session_id)

        values = {}
        if audio_duration_seconds is not None:
            values["audio_duration_seconds"] = audio_duration_seconds
        if segment_count is not None:
            values["segment_count"] = segment_count
        if word_count is not None:
            values["word_count"] = word_count

        if values:
            stmt = (
                update(RealtimeSessionModel)
                .where(RealtimeSessionModel.id == session_uuid)
                .values(**values)
            )
            await self.db.execute(stmt)
            await self.db.commit()

    async def finalize_session(
        self,
        session_id: str,
        status: str,
        audio_duration_seconds: float | None = None,
        segment_count: int | None = None,
        word_count: int | None = None,
        audio_uri: str | None = None,
        transcript_uri: str | None = None,
        enhancement_job_id: UUID | None = None,
        error: str | None = None,
    ) -> RealtimeSessionModel | None:
        """Finalize a session on completion or error.

        Computes purge_after based on session's retention settings:
        - auto_delete: purge_after = ended_at + retention_hours
        - none: purge_after = now (immediate purge)
        - keep: purge_after stays NULL (never purge)

        Args:
            session_id: Session ID
            status: Final status (completed, error, interrupted)
            audio_duration_seconds: Final audio duration
            segment_count: Final segment count
            word_count: Final word count
            audio_uri: S3 URI for recorded audio
            transcript_uri: S3 URI for transcript
            enhancement_job_id: ID of enhancement job if created
            error: Error message if session failed

        Returns:
            Updated session or None if not found
        """
        session_uuid = self._parse_session_id(session_id)

        # Fetch session to get retention settings
        existing = await self.get_session(session_id)
        if existing is None:
            return None

        ended_at = datetime.now(UTC)
        values = {
            "status": status,
            "ended_at": ended_at,
        }

        if audio_duration_seconds is not None:
            values["audio_duration_seconds"] = audio_duration_seconds
        if segment_count is not None:
            values["segment_count"] = segment_count
        if word_count is not None:
            values["word_count"] = word_count
        if audio_uri is not None:
            values["audio_uri"] = audio_uri
        if transcript_uri is not None:
            values["transcript_uri"] = transcript_uri
        if enhancement_job_id is not None:
            values["enhancement_job_id"] = enhancement_job_id
        if error is not None:
            values["error"] = error

        # Compute purge_after based on retention settings (M25)
        purge_after = self._compute_purge_after(existing, ended_at)
        if purge_after is not None:
            values["purge_after"] = purge_after

        stmt = (
            update(RealtimeSessionModel)
            .where(RealtimeSessionModel.id == session_uuid)
            .values(**values)
            .returning(RealtimeSessionModel)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()

        session = result.scalar_one_or_none()
        if session:
            logger.info(
                "realtime_session_finalized",
                session_id=session_id,
                status=status,
                audio_duration_seconds=audio_duration_seconds,
                purge_after=purge_after.isoformat() if purge_after else None,
            )

        return session

    def _compute_purge_after(
        self,
        session: RealtimeSessionModel,
        ended_at: datetime,
    ) -> datetime | None:
        """Compute purge_after based on session's retention settings.

        Args:
            session: Session with retention settings
            ended_at: When the session ended

        Returns:
            purge_after datetime, or None if should never be purged
        """
        # Only compute purge_after if session has stored artifacts
        if not session.store_audio and not session.store_transcript:
            return None

        if session.retention_mode == RetentionMode.AUTO_DELETE.value:
            if session.retention_hours:
                return ended_at + timedelta(hours=session.retention_hours)
            # No hours specified, use default (shouldn't happen with proper validation)
            return None
        elif session.retention_mode == RetentionMode.NONE.value:
            # Immediate purge
            return ended_at
        # mode == "keep": never purge
        return None

    async def get_session(self, session_id: str) -> RealtimeSessionModel | None:
        """Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session or None if not found
        """
        session_uuid = self._parse_session_id(session_id)
        stmt = select(RealtimeSessionModel).where(
            RealtimeSessionModel.id == session_uuid
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        tenant_id: UUID,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[RealtimeSessionModel], bool]:
        """List sessions for a tenant with optional filters and cursor pagination.

        Args:
            tenant_id: Tenant UUID
            status: Filter by status
            since: Filter sessions started after this time
            until: Filter sessions started before this time
            limit: Max results
            cursor: Pagination cursor (format: started_at_iso:session_id)

        Returns:
            Tuple of (sessions, has_more)
        """
        # Build base query
        stmt = select(RealtimeSessionModel).where(
            RealtimeSessionModel.tenant_id == tenant_id
        )

        if status:
            stmt = stmt.where(RealtimeSessionModel.status == status)
        if since:
            stmt = stmt.where(RealtimeSessionModel.started_at >= since)
        if until:
            stmt = stmt.where(RealtimeSessionModel.started_at <= until)

        # Apply cursor filter (sessions older than cursor)
        if cursor:
            decoded = self._decode_session_cursor(cursor)
            if decoded:
                cursor_started_at, cursor_id = decoded
                # Get sessions started before cursor OR same time but with smaller ID
                stmt = stmt.where(
                    (RealtimeSessionModel.started_at < cursor_started_at)
                    | (
                        (RealtimeSessionModel.started_at == cursor_started_at)
                        & (RealtimeSessionModel.id < cursor_id)
                    )
                )

        # Fetch limit + 1 to determine has_more, order by started_at descending
        stmt = stmt.order_by(
            RealtimeSessionModel.started_at.desc(),
            RealtimeSessionModel.id.desc(),
        ).limit(limit + 1)

        result = await self.db.execute(stmt)
        sessions = list(result.scalars().all())

        has_more = len(sessions) > limit
        if has_more:
            sessions = sessions[:limit]

        return sessions, has_more

    def encode_session_cursor(self, session: RealtimeSessionModel) -> str:
        """Encode a cursor from a session's started_at and id."""
        return f"{session.started_at.isoformat()}:{session.id}"

    def _decode_session_cursor(self, cursor: str) -> tuple[datetime, UUID] | None:
        """Decode a cursor into started_at and id."""
        try:
            parts = cursor.rsplit(":", 1)
            if len(parts) != 2:
                return None
            started_at = datetime.fromisoformat(parts[0])
            session_id = UUID(parts[1])
            return started_at, session_id
        except (ValueError, TypeError):
            return None

    async def delete_session(
        self,
        session_id: str,
        tenant_id: UUID,
    ) -> bool:
        """Delete a session by ID.

        Only allows deletion of non-active sessions (completed, error, interrupted).

        Args:
            session_id: Session ID
            tenant_id: Tenant UUID (for access control)

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If session is still active
        """
        from sqlalchemy import delete

        session_uuid = self._parse_session_id(session_id)

        # First check if session exists and verify access + status
        session = await self.get_session(session_id)

        if session is None:
            return False

        # Verify tenant access
        if session.tenant_id != tenant_id:
            return False

        # Don't allow deletion of active sessions
        if session.status == "active":
            raise ValueError("Cannot delete active session")

        # Delete the session
        stmt = delete(RealtimeSessionModel).where(
            RealtimeSessionModel.id == session_uuid
        )
        await self.db.execute(stmt)
        await self.db.commit()

        logger.info(
            "realtime_session_deleted",
            session_id=session_id,
            tenant_id=str(tenant_id),
        )

        return True

    def _parse_session_id(self, session_id: str) -> UUID:
        """Parse session ID string to UUID.

        Delegates to dalston.common.utils.parse_session_id.
        """
        return parse_session_id(session_id)
