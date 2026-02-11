"""Service for persisting realtime sessions to PostgreSQL and S3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
        store_audio: bool = False,
        store_transcript: bool = False,
        enhance_on_end: bool = False,
        previous_session_id: UUID | None = None,
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
        utterance_count: int | None = None,
        word_count: int | None = None,
    ) -> None:
        """Update session statistics.

        Called periodically during the session.

        Args:
            session_id: Session ID
            audio_duration_seconds: Total audio duration
            utterance_count: Number of utterances
            word_count: Number of words
        """
        session_uuid = self._parse_session_id(session_id)

        values = {}
        if audio_duration_seconds is not None:
            values["audio_duration_seconds"] = audio_duration_seconds
        if utterance_count is not None:
            values["utterance_count"] = utterance_count
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
        utterance_count: int | None = None,
        word_count: int | None = None,
        audio_uri: str | None = None,
        transcript_uri: str | None = None,
        enhancement_job_id: UUID | None = None,
        error: str | None = None,
    ) -> RealtimeSessionModel | None:
        """Finalize a session on completion or error.

        Args:
            session_id: Session ID
            status: Final status (completed, error, interrupted)
            audio_duration_seconds: Final audio duration
            utterance_count: Final utterance count
            word_count: Final word count
            audio_uri: S3 URI for recorded audio
            transcript_uri: S3 URI for transcript
            enhancement_job_id: ID of enhancement job if created
            error: Error message if session failed

        Returns:
            Updated session or None if not found
        """
        session_uuid = self._parse_session_id(session_id)

        values = {
            "status": status,
            "ended_at": datetime.now(UTC),
        }

        if audio_duration_seconds is not None:
            values["audio_duration_seconds"] = audio_duration_seconds
        if utterance_count is not None:
            values["utterance_count"] = utterance_count
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
            )

        return session

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
        offset: int = 0,
    ) -> tuple[list[RealtimeSessionModel], int]:
        """List sessions for a tenant with optional filters.

        Args:
            tenant_id: Tenant UUID
            status: Filter by status
            since: Filter sessions started after this time
            until: Filter sessions started before this time
            limit: Max results
            offset: Pagination offset

        Returns:
            Tuple of (sessions, total_count)
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

        # Get total count
        from sqlalchemy import func as sa_func

        count_stmt = select(sa_func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(RealtimeSessionModel.started_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await self.db.execute(stmt)
        sessions = list(result.scalars().all())

        return sessions, total

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

        Session IDs can be:
        - sess_<hex16> format (e.g., sess_abc123def456)
        - Raw UUID string

        Args:
            session_id: Session ID string

        Returns:
            UUID
        """
        if session_id.startswith("sess_"):
            # Extract hex part and pad to 32 chars for UUID
            hex_part = session_id[5:]
            # Pad to 32 chars
            padded = hex_part.ljust(32, "0")
            return UUID(padded)
        else:
            return UUID(session_id)
