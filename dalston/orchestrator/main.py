"""Orchestrator engine_id mode dispatch entrypoint.

Also provides compatibility exports for tests and callers that patch symbols
from ``dalston.orchestrator.main``.
"""

from __future__ import annotations

from redis import asyncio as aioredis

from dalston.common.durable_events import DurableEventEnvelope
from dalston.common.registry import UnifiedEngineRegistry
from dalston.config import Settings, get_settings
from dalston.orchestrator import distributed_main as _distributed_main

# Compatibility re-exports.
EventSchemaError = _distributed_main.EventSchemaError
HandlerExecutionError = _distributed_main.HandlerExecutionError
UnknownEventTypeError = _distributed_main.UnknownEventTypeError
ack_event = _distributed_main.ack_event
async_session = _distributed_main.async_session
handle_job_created = _distributed_main.handle_job_created
move_event_to_dlq = _distributed_main.move_event_to_dlq
_dispatch_event_dict = _distributed_main._dispatch_event_dict


def _sync_distributed_patch_points() -> None:
    """Mirror compatibility symbols into distributed_main before invocation.

    This bridge only syncs the legacy names re-exported from this module.
    Patch any other distributed engine_id globals directly on
    ``dalston.orchestrator.distributed_main``.
    """
    _distributed_main.handle_job_created = handle_job_created
    _distributed_main.async_session = async_session
    _distributed_main.ack_event = ack_event
    _distributed_main.move_event_to_dlq = move_event_to_dlq
    _distributed_main._dispatch_event_dict = _dispatch_event_dict


async def _dispatch_event(
    data: str,
    redis: aioredis.Redis,
    settings: Settings,
    batch_registry: UnifiedEngineRegistry,
) -> None:
    _sync_distributed_patch_points()
    await _distributed_main._dispatch_event(data, redis, settings, batch_registry)


async def _process_durable_event(
    envelope: DurableEventEnvelope,
    redis: aioredis.Redis,
    settings: Settings,
    batch_registry: UnifiedEngineRegistry,
    consumer_id: str,
    source: str,
) -> None:
    _sync_distributed_patch_points()
    await _distributed_main._process_durable_event(
        envelope=envelope,
        redis=redis,
        settings=settings,
        batch_registry=batch_registry,
        consumer_id=consumer_id,
        source=source,
    )


def main() -> None:
    """Dispatch to distributed or lite orchestrator entrypoints."""
    settings = get_settings()
    if settings.runtime_mode == "lite":
        from dalston.orchestrator.lite_main import main as lite_main

        lite_main()
        return

    from dalston.orchestrator.distributed_main import main as distributed_main

    distributed_main()


if __name__ == "__main__":
    main()
