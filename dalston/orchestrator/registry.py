"""Batch engine registry — thin re-export of the unified engine registry (M69).

Legacy ``BatchEngineRegistry`` / ``BatchEngineState`` removed. All consumers
use ``UnifiedEngineRegistry`` and ``EngineRecord`` directly.
"""

from dalston.common.registry import EngineRecord, UnifiedEngineRegistry

__all__ = ["EngineRecord", "UnifiedEngineRegistry"]
