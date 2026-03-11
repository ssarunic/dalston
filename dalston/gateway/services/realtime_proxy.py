"""Shared lifecycle core for all WebSocket realtime adapters.

This module extracts the common allocation/keepalive/release pattern that was
duplicated across the Dalston-native, OpenAI-compatible, and ElevenLabs-compatible
WebSocket handlers into a single ``RealtimeProxy`` service (M65).

Responsibilities of this module
--------------------------------
- Allocate a realtime worker from the session router.
- Start a keepalive task that extends the Redis session TTL.
- Optionally create and finalise a DB session record.
- Release the worker on any exit path.
- Decrement the rate-limit concurrent-sessions counter.

Responsibilities of the caller (adapter handlers)
--------------------------------------------------
- Authenticate the WebSocket connection.
- Accept the connection.
- Check and increment the rate-limit session counter.
- Resolve routing parameters (model, engine_id).
- Provide a ``connect`` coroutine that performs the protocol-specific proxy.
- Send any protocol-specific opening message before calling ``run()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from dalston.common.ws_close_codes import WS_CLOSE_SERVICE_UNAVAILABLE
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.api.v1._realtime_common import (
    RealtimeLagExceededError,
    keep_session_alive,
)
from dalston.gateway.services.realtime_sessions import RealtimeSessionService

if TYPE_CHECKING:
    from dalston.gateway.api.v1._realtime_common import RTRoutingParams
    from dalston.orchestrator.session_allocator import WorkerAllocation
    from dalston.orchestrator.session_coordinator import SessionCoordinator

logger = structlog.get_logger()

# Type alias for the protocol-specific proxy callable.
# Receives the client WebSocket and the allocated worker info; returns the
# session.end data dict emitted by the worker, or None if unavailable.
ConnectFn = Callable[["WebSocket", "WorkerAllocation"], Awaitable[dict | None]]


@dataclass
class ProxySessionParams:
    """Parameters needed to create and finalise a DB session record.

    Pass this to ``RealtimeProxy.run()`` to enable PostgreSQL persistence.
    Leave it as ``None`` for adapters that do not persist session records
    (e.g. the ElevenLabs adapter which relies on Redis-only state).
    """

    tenant_id: UUID
    client_ip: str
    language: str
    model: str
    created_by_key_id: UUID
    encoding: str | None = None
    sample_rate: int | None = None
    retention: int | None = None
    previous_session_id: UUID | None = None


class RealtimeProxy:
    """Shared session lifecycle manager for all realtime WebSocket adapters.

    Usage::

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=ws,
            session_router=session_router,
            routing_params=rt,
            language=language,
            tenant_id=api_key.tenant_id,
            connect=lambda ws, alloc: _my_protocol_proxy(ws, alloc, ...),
            session_params=ProxySessionParams(...),
            on_no_capacity=lambda: ws.send_json({...}),
        )

    The caller is responsible for:

    - Authenticating the WebSocket and obtaining ``api_key``.
    - Accepting the connection (``await websocket.accept()``).
    - Checking / incrementing the concurrent-session rate-limit counter.
    - Resolving routing parameters (``RTRoutingParams``).
    - Sending any protocol-specific opening message before calling ``run()``.

    ``run()`` manages everything after that:

    1. Worker acquisition.
    2. Optional DB session creation.
    3. Keepalive task.
    4. Calling ``connect(websocket, allocation)`` (the protocol-specific proxy).
    5. Exception handling (lag, disconnect, generic error).
    6. Keepalive cancellation.
    7. Worker release.
    8. Optional DB session stats update + finalisation.
    9. Rate-limit counter decrement (always, even on early exit).
    """

    async def run(
        self,
        *,
        websocket: WebSocket,
        session_router: SessionCoordinator,
        routing_params: RTRoutingParams,
        language: str,
        connect: ConnectFn,
        session_params: ProxySessionParams | None = None,
        on_no_capacity: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Run the allocation/keepalive/proxy/release lifecycle.

        The caller is responsible for:

        - Accepting the WebSocket (``await websocket.accept()``).
        - Incrementing the rate-limit concurrent-sessions counter.
        - Wrapping this call in a ``try/finally`` that decrements the counter
          so that early validation returns also trigger the decrement.

        Args:
            websocket: The client WebSocket connection (already accepted).
            session_router: SessionRouter used for worker allocation.
            routing_params: Model/engine_id routing resolution from ``resolve_rt_routing()``.
            language: Language code to forward to the worker (e.g. ``"auto"``).
            connect: Protocol-specific proxy coroutine. Called with
                ``(websocket, allocation)``; returns ``session_end_data | None``.
            session_params: If provided, a PostgreSQL session record is created
                at start and finalised on exit.
            on_no_capacity: Optional async callable invoked (before the close
                frame) when no worker capacity is available, allowing the
                adapter to send a protocol-specific error message.
        """
        client_ip = websocket.client.host if websocket.client else "unknown"

        allocation = await session_router.acquire_worker(
            language=language,
            model=routing_params.routing_model,
            client_ip=client_ip,
            engine_id=routing_params.model_engine_id,
            valid_engine_ids=routing_params.valid_engine_ids,
        )

        if allocation is None:
            if on_no_capacity is not None:
                await on_no_capacity()
            await websocket.close(
                code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="No capacity"
            )
            return

        log = logger.bind(
            session_id=allocation.session_id,
            instance=allocation.instance,
            client_ip=client_ip,
        )
        log.info("session_allocated")

        await self._run_allocated(
            websocket=websocket,
            allocation=allocation,
            session_router=session_router,
            language=language,
            connect=connect,
            session_params=session_params,
            log=log,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_allocated(
        self,
        *,
        websocket: WebSocket,
        allocation: WorkerAllocation,
        session_router: SessionCoordinator,
        language: str,
        connect: ConnectFn,
        session_params: ProxySessionParams | None,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        """Manage the session lifecycle once a worker has been allocated."""
        session_service, db_gen = await self._create_db_session(
            allocation=allocation,
            language=language,
            session_params=session_params,
            log=log,
        )

        session_status = "completed"
        session_error: str | None = None
        session_end_data: dict | None = None
        keepalive_task: asyncio.Task[None] | None = None

        try:
            keepalive_task = asyncio.create_task(
                keep_session_alive(session_router, allocation.session_id)
            )
            structlog.contextvars.bind_contextvars(session_id=allocation.session_id)

            session_end_data = await connect(websocket, allocation)

        except RealtimeLagExceededError:
            log.warning("session_lag_exceeded")
            session_error = "lag_exceeded"
            session_status = "error"
            session_end_data = None

        except WebSocketDisconnect:
            log.info("client_disconnected")
            session_status = "interrupted"

        except Exception as exc:
            log.error("session_error", error=str(exc))
            session_error = str(exc)
            session_status = "error"

        finally:
            if keepalive_task is not None:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            await session_router.release_worker(allocation.session_id)
            log.info("session_released")

            if session_service is not None:
                await self._finalize_db_session(
                    allocation=allocation,
                    session_service=session_service,
                    db_gen=db_gen,
                    session_end_data=session_end_data,
                    session_status=session_status,
                    session_error=session_error,
                    log=log,
                )

    async def _create_db_session(
        self,
        *,
        allocation: WorkerAllocation,
        language: str,
        session_params: ProxySessionParams | None,
        log: structlog.stdlib.BoundLogger,
    ) -> tuple[RealtimeSessionService | None, object]:
        """Create a PostgreSQL session record.

        Returns:
            ``(session_service, db_gen)`` if ``session_params`` was provided
            and creation succeeded; ``(None, None)`` otherwise.
        """
        if session_params is None:
            return None, None

        db_gen = None
        try:
            db_gen = _get_db()
            db_session = await db_gen.__anext__()
            settings = get_settings()
            session_service = RealtimeSessionService(db_session, settings)

            kwargs: dict = {
                "session_id": allocation.session_id,
                "tenant_id": session_params.tenant_id,
                "instance": allocation.instance,
                "client_ip": session_params.client_ip,
                "language": language,
                "model": session_params.model,
                "engine_id": allocation.engine_id,
                "created_by_key_id": session_params.created_by_key_id,
            }
            if session_params.encoding is not None:
                kwargs["encoding"] = session_params.encoding
            if session_params.sample_rate is not None:
                kwargs["sample_rate"] = session_params.sample_rate
            if session_params.retention is not None:
                kwargs["retention"] = session_params.retention
            if session_params.previous_session_id is not None:
                kwargs["previous_session_id"] = session_params.previous_session_id

            await session_service.create_session(**kwargs)
            return session_service, db_gen

        except Exception as exc:
            log.warning("session_db_create_failed", error=str(exc))
            if db_gen is not None:
                try:
                    await db_gen.aclose()
                except Exception:
                    pass
            # Session still works via Redis even without DB record.
            return None, None

    async def _finalize_db_session(
        self,
        *,
        allocation: WorkerAllocation,
        session_service: RealtimeSessionService,
        db_gen: object,
        session_end_data: dict | None,
        session_status: str,
        session_error: str | None,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        """Update stats and finalise the DB session record."""
        audio_uri: str | None = None
        transcript_uri: str | None = None

        if session_end_data:
            try:
                log.debug("session_end_data_content", data=session_end_data)

                audio_duration = session_end_data.get("total_audio_seconds", 0)
                segments = session_end_data.get("segments", [])
                transcript = session_end_data.get("transcript", "")
                word_count = len(transcript.split()) if transcript else 0

                audio_uri = session_end_data.get("audio_uri")
                transcript_uri = session_end_data.get("transcript_uri")

                log.info(
                    "session_stats_captured",
                    audio_duration=audio_duration,
                    segment_count=len(segments),
                    word_count=word_count,
                    audio_uri=audio_uri,
                    transcript_uri=transcript_uri,
                )

                await session_service.update_stats(
                    session_id=allocation.session_id,
                    audio_duration_seconds=audio_duration,
                    segment_count=len(segments),
                    word_count=word_count,
                )
            except Exception as exc:
                log.warning("session_stats_update_failed", error=str(exc))
        else:
            log.warning(
                "session_end_data_missing",
                msg="No session.end data received from worker",
            )
            if session_status == "completed":
                session_status = "error"
                session_error = session_error or "worker_no_session_end"

        try:
            await session_service.finalize_session(
                session_id=allocation.session_id,
                status=session_status,
                error=session_error,
                audio_uri=audio_uri,
                transcript_uri=transcript_uri,
            )
        except Exception as exc:
            log.warning("session_db_finalize_failed", error=str(exc))

        if db_gen is not None:
            try:
                await db_gen.aclose()  # type: ignore[union-attr]
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton – avoids constructing a new instance per request
# ---------------------------------------------------------------------------

_proxy = RealtimeProxy()


def get_realtime_proxy() -> RealtimeProxy:
    """Return the shared ``RealtimeProxy`` singleton."""
    return _proxy
