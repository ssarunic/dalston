"""Async model manager wrapper for real-time engines.

This module provides an async wrapper around the synchronous ModelManager
from the engine SDK, allowing RT engines to use the same model loading
and eviction infrastructure as batch engines.

Example usage:
    from dalston.engine_sdk.managers import FasterWhisperModelManager
    from dalston.realtime_sdk.model_manager import AsyncModelManager

    class MyRealtimeEngine(RealtimeEngine):
        async def setup_models(self):
            sync_manager = FasterWhisperModelManager.from_env()
            self._model_manager = AsyncModelManager(sync_manager)

        async def transcribe(self, audio, params):
            model_id = params.loaded_model_id or "default"
            model = await self._model_manager.acquire(model_id)
            try:
                return model.transcribe(audio, ...)
            finally:
                await self._model_manager.release(model_id)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Generic, TypeVar

import structlog

if TYPE_CHECKING:
    from dalston.engine_sdk.model_manager import ModelManager

T = TypeVar("T")

logger = structlog.get_logger()


class AsyncModelManager(Generic[T]):
    """Async wrapper for ModelManager.

    Wraps the synchronous ModelManager from engine_sdk for use in async
    real-time engines. Model loading/unloading operations are offloaded
    to a thread pool to avoid blocking the event loop.

    The underlying ModelManager handles:
    - TTL-based eviction of idle models
    - LRU eviction when at max capacity
    - Reference counting to prevent eviction during use
    - Optional S3 model storage

    Attributes:
        manager: The underlying synchronous ModelManager
    """

    def __init__(self, manager: ModelManager[T]) -> None:
        """Initialize async wrapper.

        Args:
            manager: The synchronous ModelManager to wrap
        """
        self._manager = manager

    @property
    def manager(self) -> ModelManager[T]:
        """Access underlying synchronous manager."""
        return self._manager

    async def acquire(self, model_id: str) -> T:
        """Acquire a model for use, loading if necessary.

        This increments the reference count to prevent eviction during use.
        Caller MUST call release() when done.

        Model loading happens in a thread pool to avoid blocking the event loop.

        Args:
            model_id: Identifier of the model to acquire

        Returns:
            The loaded model object

        Raises:
            RuntimeError: If max_loaded models are in use and none can be evicted
            Exception: If model loading fails
        """
        # Check if model is already loaded (fast path, no thread switch needed)
        if self._manager.is_loaded(model_id):
            # Model is loaded, acquire is fast (just ref count increment)
            return self._manager.acquire(model_id)

        # Model needs loading - offload to thread pool
        logger.info("acquiring_model_async", model_id=model_id)
        return await asyncio.to_thread(self._manager.acquire, model_id)

    async def release(self, model_id: str) -> None:
        """Release a model reference.

        This decrements the reference count. If count reaches 0, the model
        becomes eligible for TTL eviction.

        This is a fast operation (just decrement) so no thread switch needed.

        Args:
            model_id: Identifier of the model to release
        """
        self._manager.release(model_id)

    def is_loaded(self, model_id: str) -> bool:
        """Check if a model is currently loaded.

        Args:
            model_id: Identifier of the model to check

        Returns:
            True if the model is loaded
        """
        return self._manager.is_loaded(model_id)

    def loaded_models(self) -> list[str]:
        """Return list of currently loaded model IDs.

        Returns:
            List of model IDs that are currently loaded
        """
        return self._manager.loaded_models()

    def get_stats(self) -> dict:
        """Return current manager statistics.

        Returns:
            Dict with manager state for monitoring/debugging
        """
        return self._manager.get_stats()

    def get_local_cache_stats(self) -> dict | None:
        """Get local model cache statistics from S3ModelStorage.

        Returns:
            Dictionary with cache stats if S3 storage is configured,
            None otherwise.
        """
        if hasattr(self._manager, "get_local_cache_stats"):
            return self._manager.get_local_cache_stats()
        return None

    async def shutdown(self) -> None:
        """Shutdown manager and unload all models.

        Call this during engine shutdown to ensure clean cleanup.
        Runs in thread pool to handle potential blocking operations.
        """
        logger.info("async_model_manager_shutdown")
        await asyncio.to_thread(self._manager.shutdown)
