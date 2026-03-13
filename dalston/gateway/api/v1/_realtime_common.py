"""Shared helpers for real-time WebSocket endpoints.

Used by realtime.py, elevenlabs (realtime.py), and openai_realtime.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from dalston.common.redis import get_redis as _get_redis
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
        # Auto-select: pick the largest ready streaming model from the registry (M48).
        # This ensures workers load from S3, not directly from HuggingFace.
        try:
            async for db in _get_db():
                downloaded_models = await ModelRegistryService().list_models(
                    db, stage="transcribe", status="ready"
                )
                rt_models = [m for m in downloaded_models if m.native_streaming]
                candidates = rt_models if rt_models else list(downloaded_models)

                # If a specific language is requested, prefer models that support it
                if language and language != "auto" and candidates:
                    lang_candidates = [
                        m
                        for m in candidates
                        if not m.languages or language in m.languages
                    ]
                    if lang_candidates:
                        candidates = lang_candidates

                if candidates:
                    largest = max(candidates, key=lambda m: m.size_bytes or 0)
                    routing_model = largest.id
                    model_engine_id = largest.engine_id
                    effective_model = largest.id
                    model_languages = largest.languages
                    logger.info(
                        "auto_selected_rt_model",
                        model_id=largest.id,
                        engine_id=largest.engine_id,
                        size_mb=round((largest.size_bytes or 0) / 1024 / 1024, 1),
                    )

                valid_engine_ids = {
                    m.engine_id for m in downloaded_models if m.engine_id
                }
                break
        except Exception as e:
            logger.warning("registry_lookup_failed", error=str(e))

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
