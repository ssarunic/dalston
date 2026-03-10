"""Realtime worker registry — thin re-export of the unified engine registry (M69).

Legacy ``WorkerRegistry`` / ``WorkerState`` removed. All consumers use
``UnifiedEngineRegistry`` and ``EngineRecord`` directly.

Redis key constants are kept here because ``session_allocator`` and
``session_health`` still write/read session-tracking keys under the
``dalston:realtime:*`` namespace (session tracking is separate from worker
registration and is unchanged by M69).
"""

from dalston.common.registry import EngineRecord, UnifiedEngineRegistry

# ---------------------------------------------------------------------------
# Redis key constants (session-tracking keys, still in active use)
# ---------------------------------------------------------------------------

INSTANCE_SET_KEY = "dalston:realtime:instances"
INSTANCE_KEY_PREFIX = "dalston:realtime:instance:"
INSTANCE_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
ACTIVE_SESSIONS_KEY = "dalston:realtime:sessions:active"
EVENTS_CHANNEL = "dalston:realtime:events"

__all__ = [
    "EngineRecord",
    "UnifiedEngineRegistry",
    "INSTANCE_SET_KEY",
    "INSTANCE_KEY_PREFIX",
    "INSTANCE_SESSIONS_SUFFIX",
    "SESSION_KEY_PREFIX",
    "ACTIVE_SESSIONS_KEY",
    "EVENTS_CHANNEL",
]
