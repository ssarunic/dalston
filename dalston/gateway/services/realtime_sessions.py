"""Service for persisting realtime sessions to PostgreSQL and S3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import (
    ArtifactOwnerType,
    retention_to_ttl_seconds,
)
from dalston.common.utils import parse_session_id
from dalston.db.models import RealtimeSessionModel
from dalston.gateway.services.artifacts import ArtifactService

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
        retention: int = 30,
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
            retention: Retention in days (0=transient, -1=permanent, N=days)
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
            retention=retention,
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
            retention=retention,
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
        if error is not None:
            values["error"] = error

        # Compute purge_after based on retention settings (M25 - V1)
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

        # Mark V2 artifacts as available and compute their purge_after (M35)
        # This prevents the race condition where artifacts could be purged
        # before the session finishes writing them.
        artifact_service = ArtifactService()
        artifacts_marked = await artifact_service.mark_owner_artifacts_available(
            self.db, ArtifactOwnerType.SESSION, session_uuid, available_at=ended_at
        )
        if artifacts_marked > 0:
            logger.info(
                "session_artifacts_marked_available",
                session_id=session_id,
                count=artifacts_marked,
            )

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
        # Use the integer retention field: 0=transient, -1=permanent, N=days
        retention_days = session.retention if session.retention is not None else 30
        ttl_seconds = retention_to_ttl_seconds(retention_days)

        if ttl_seconds == 0:
            # 0 = transient - immediate purge (nothing was stored)
            return ended_at
        elif ttl_seconds is None:
            # -1 = permanent - never purge
            return None
        else:
            # N = days - purge after N days
            return ended_at + timedelta(seconds=ttl_seconds)

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
        sort: Literal["started_desc", "started_asc"] = "started_desc",
    ) -> tuple[list[RealtimeSessionModel], bool]:
        """List sessions for a tenant with optional filters and cursor pagination.

        Args:
            tenant_id: Tenant UUID
            status: Filter by status
            since: Filter sessions started after this time
            until: Filter sessions started before this time
            limit: Max results
            cursor: Pagination cursor (format: started_at_iso:session_id)
            sort: Sort order for started_at

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

        # Apply cursor filter
        if cursor:
            decoded = self._decode_session_cursor(cursor)
            if decoded:
                cursor_started_at, cursor_id = decoded
                if sort == "started_asc":
                    # Get sessions started after cursor OR same time but with larger ID
                    stmt = stmt.where(
                        (RealtimeSessionModel.started_at > cursor_started_at)
                        | (
                            (RealtimeSessionModel.started_at == cursor_started_at)
                            & (RealtimeSessionModel.id > cursor_id)
                        )
                    )
                else:
                    # Get sessions started before cursor OR same time but with smaller ID
                    stmt = stmt.where(
                        (RealtimeSessionModel.started_at < cursor_started_at)
                        | (
                            (RealtimeSessionModel.started_at == cursor_started_at)
                            & (RealtimeSessionModel.id < cursor_id)
                        )
                    )

        # Fetch limit + 1 to determine has_more
        if sort == "started_asc":
            stmt = stmt.order_by(
                RealtimeSessionModel.started_at.asc(),
                RealtimeSessionModel.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                RealtimeSessionModel.started_at.desc(),
                RealtimeSessionModel.id.desc(),
            )
        stmt = stmt.limit(limit + 1)

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
