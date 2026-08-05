"""Shared helpers for real-time WebSocket endpoints.

Used by realtime.py, elevenlabs (realtime.py), and openai_realtime.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog

from dalston.common.redis import get_redis as _get_redis
from dalston.common.registry import UnifiedEngineRegistry
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.services.auth import AuthService
from dalston.gateway.services.model_registry import ModelRegistryService
from dalston.gateway.services.rate_limiter import RedisRateLimiter

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Shared realtime exceptions/helpers
# ---------------------------------------------------------------------------


class RealtimeLagExceededError(RuntimeError):
    """Raised when worker closes a session due to lag budget exceedance."""


def get_worker_close_code(worker_ws: Any) -> int | None:
    """Best-effort extraction of close code across websocket client variants."""
    close_code = getattr(worker_ws, "close_code", None)
    if close_code is not None:
        return close_code

    close = getattr(worker_ws, "close", None)
    if close is not None:
        nested_close_code = getattr(close, "code", None)
        if nested_close_code is not None:
            return nested_close_code

    return None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def get_realtime_auth_service() -> tuple[AuthService, Any]:
    """Get AuthService for WebSocket authentication.

    Returns:
        Tuple of (AuthService, db_gen). Caller must call db_gen.aclose().
    """
    redis = await _get_redis()
    db_gen = _get_db()
    db = await db_gen.__anext__()
    return AuthService(db, redis), db_gen


# ---------------------------------------------------------------------------
# Rate-limit helpers
# ---------------------------------------------------------------------------


async def decrement_realtime_session_count(tenant_id: UUID) -> None:
    """Decrement concurrent session count when a WebSocket connection closes."""
    try:
        settings = get_settings()
        redis = await _get_redis()
        rate_limiter = RedisRateLimiter(
            redis=redis,
            requests_per_minute=settings.rate_limit_requests_per_minute,
            max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
            max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
        )
        await rate_limiter.decrement_concurrent_sessions(tenant_id)
    except Exception as e:
        logger.warning("failed_to_decrement_session_count", error=str(e))


# ---------------------------------------------------------------------------
# Session keepalive
# ---------------------------------------------------------------------------


async def keep_session_alive(
    session_router,
    session_id: str,
    interval: int = 60,
) -> None:
    """Periodically extend session TTL to prevent expiration.

    Sessions have a 5-minute TTL in Redis. For long-running sessions,
    this task extends the TTL every interval seconds to prevent the
    health monitor from treating the session as orphaned.

    Args:
        session_router: SessionRouter instance
        session_id: Session ID to keep alive
        interval: How often to extend in seconds (default: 60s)
    """
    import asyncio

    while True:
        await asyncio.sleep(interval)
        try:
            await session_router.extend_session_ttl(session_id)
            logger.debug("session_ttl_extended", session_id=session_id)
        except Exception as e:
            logger.warning(
                "session_ttl_extend_failed", session_id=session_id, error=str(e)
            )


# ---------------------------------------------------------------------------
# RT routing
# ---------------------------------------------------------------------------


class RTRoutingParams:
    """Resolved routing parameters for a real-time session."""

    __slots__ = (
        "routing_model",
        "model_engine_id",
        "valid_engine_ids",
        "effective_model",
    )

    def __init__(
        self,
        routing_model: str | None,
        model_engine_id: str | None,
        valid_engine_ids: set[str] | None,
        effective_model: str,
    ) -> None:
        self.routing_model = routing_model
        self.model_engine_id = model_engine_id
        self.valid_engine_ids = valid_engine_ids
        self.effective_model = effective_model


def select_rt_model(
    models: Sequence[Any],
    live_rt_engine_ids: set[str],
    *,
    language: str = "auto",
) -> Any | None:
    """Pick the best realtime model, or *None* if none can be served.

    The hard constraint is that the model's engine has a **live realtime
    worker** — nothing else can serve the session, whatever the model's
    own capabilities.

    ``native_streaming`` is a *preference*, not a gate. Cache-aware
    RNNT/TDT decoding gives lower latency and real partial results, but
    the realtime SDK also wraps non-native models with VAD segmentation
    (slice the stream at the pause, transcribe each segment), so they are
    usable too — just second choice.

    Gating on the flag was wrong twice over: almost no model YAML declares
    it, so the gate excluded nearly everything, and the old fallback then
    dropped the realtime constraint entirely and took the largest model
    overall — routing to engines with no worker at all and failing at
    allocation with a misleading "no realtime workers available". See
    M102.

    Args:
        models: Ready transcribe models from the registry.
        live_rt_engine_ids: engine_ids with an available realtime worker.
        language: BCP-47 code, or ``"auto"`` for no language preference.

    Returns:
        The selected model, or *None* when no model's engine can serve
        realtime. Callers should report that reason rather than routing
        to an unservable engine.
    """
    servable = [m for m in models if m.engine_id in live_rt_engine_ids]

    # Language may narrow the set, but must never widen it past servable —
    # falling back to an engine that cannot serve is what produced the
    # misleading error.
    if language and language != "auto":
        by_language = [
            m for m in servable if not m.languages or language in m.languages
        ]
        candidates = by_language or servable
    else:
        candidates = servable

    if not candidates:
        return None

    # Native streaming first, then larger (accuracy) within the same tier —
    # preserving the previous heuristic now the set is correctly constrained.
    return max(candidates, key=lambda m: (bool(m.native_streaming), m.size_bytes or 0))


async def resolve_rt_routing(
    requested_model: str | None,
    language: str = "auto",
) -> RTRoutingParams:
    """Resolve routing parameters for a real-time session.

    When a specific model is requested, looks up its engine_id for worker matching.
    When no model is requested (None / empty), auto-selects the largest ready
    streaming model from the registry (M48) and collects valid engine_ids for
    fallback routing.

    If a specific language is requested (not "auto"), validates that the resolved
    model supports it. Raises ValueError if the model cannot handle the language.

    Args:
        requested_model: Model ID from client, or None/empty for auto-select.
        language: Requested language code, or "auto" for auto-detection.

    Returns:
        Routing parameters to pass to the session allocator.

    Raises:
        ValueError: If the resolved model does not support the requested language.
    """
    routing_model = requested_model or None
    model_engine_id: str | None = None
    valid_engine_ids: set[str] | None = None
    effective_model: str = requested_model or ""
    model_languages: list[str] | None = None

    if routing_model:
        try:
            async for db in _get_db():
                model_entry = await ModelRegistryService().get_model(db, routing_model)
                if model_entry:
                    model_engine_id = model_entry.engine_id
                    model_languages = model_entry.languages
                break
        except Exception as e:
            logger.warning("model_lookup_failed", model=routing_model, error=str(e))
    else:
        # Auto-select — see :func:`select_rt_model` for the rule.
        no_servable_engine = False
        try:
            # Discover which engine_ids have live RT workers
            redis = await _get_redis()
            registry = UnifiedEngineRegistry(redis)
            rt_workers = await registry.get_available(interface="realtime")
            live_rt_engine_ids = {w.engine_id for w in rt_workers}

            async for db in _get_db():
                downloaded_models = await ModelRegistryService().list_models(
                    db, stage="transcribe", status="ready"
                )
                best = select_rt_model(
                    downloaded_models, live_rt_engine_ids, language=language
                )

                if best is not None:
                    routing_model = best.id
                    model_engine_id = best.engine_id
                    effective_model = best.id
                    model_languages = best.languages
                    logger.info(
                        "auto_selected_rt_model",
                        model_id=best.id,
                        engine_id=best.engine_id,
                        native_streaming=bool(best.native_streaming),
                        size_mb=round((best.size_bytes or 0) / 1024 / 1024, 1),
                    )
                elif downloaded_models:
                    # Models exist, but none belong to an engine that is
                    # serving realtime. Report that rather than routing to
                    # one of them and failing at allocation.
                    no_servable_engine = True
                    logger.warning(
                        "no_realtime_servable_model",
                        live_rt_engine_ids=sorted(live_rt_engine_ids),
                        downloaded_engine_ids=sorted(
                            {m.engine_id for m in downloaded_models if m.engine_id}
                        ),
                    )

                valid_engine_ids = {
                    m.engine_id for m in downloaded_models if m.engine_id
                }
                break
        except Exception as e:
            logger.warning("registry_lookup_failed", error=str(e))

        # Raised outside the try so the deliberate failure is not swallowed
        # by the registry-lookup handler above.
        if no_servable_engine:
            raise ValueError(
                "No realtime-capable engine is running for any downloaded "
                "transcribe model. Start a realtime worker for one of them, "
                "or select a model explicitly."
            )

    # Validate language support against the resolved model
    if (
        language
        and language != "auto"
        and model_languages
        and language not in model_languages
    ):
        raise ValueError(
            f"Model '{effective_model}' does not support language '{language}'. "
            f"Supported: {', '.join(sorted(model_languages))}"
        )

    return RTRoutingParams(
        routing_model, model_engine_id, valid_engine_ids, effective_model
    )
