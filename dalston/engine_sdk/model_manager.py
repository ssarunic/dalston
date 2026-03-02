"""TTL-based model manager with reference counting and LRU eviction.

This module provides a base class for managing model lifecycles in Dalston
engines. It enables:

- **Multi-model support**: Load multiple models on a single GPU
- **TTL eviction**: Automatically unload idle models after configurable timeout
- **LRU eviction**: When at capacity, evict least-recently-used models first
- **Reference counting**: Safe eviction that waits for in-flight requests

Example usage:
    class MyModelManager(ModelManager[MyModel]):
        def _load_model(self, model_id: str) -> MyModel:
            return load_my_model(model_id)

        def _unload_model(self, model: MyModel) -> None:
            del model

    manager = MyModelManager(ttl_seconds=3600, max_loaded=2)
    model = manager.acquire("my-model")
    try:
        result = model.process(data)
    finally:
        manager.release("my-model")

Environment variables:
    DALSTON_MODEL_TTL_SECONDS: Default TTL for idle models (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model ID to preload on startup (optional)
"""

from __future__ import annotations

import gc
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")  # Model type

logger = structlog.get_logger()


@dataclass
class LoadedModel(Generic[T]):
    """Wrapper tracking a loaded model's lifecycle.

    Attributes:
        model_id: Identifier for this model
        model: The actual model object
        loaded_at: Unix timestamp when model was loaded
        last_used_at: Unix timestamp of last acquire() call
        ref_count: Number of active references (in-flight requests)
        size_bytes: Optional size estimate for monitoring
    """

    model_id: str
    model: T
    loaded_at: float
    last_used_at: float
    ref_count: int = 0
    size_bytes: int | None = None

    def touch(self) -> None:
        """Update last_used_at to current time."""
        self.last_used_at = time.time()

    @property
    def idle_seconds(self) -> float:
        """Seconds since last use."""
        return time.time() - self.last_used_at

    @property
    def loaded_seconds(self) -> float:
        """Seconds since model was loaded."""
        return time.time() - self.loaded_at


class ModelManager(ABC, Generic[T]):
    """Base class for TTL-based model management.

    This class provides thread-safe model lifecycle management with:

    - **Reference counting**: Models are only evicted when ref_count is 0
    - **LRU eviction**: When max_loaded is reached, evict least-recently-used
    - **TTL eviction**: Background thread evicts models idle longer than ttl_seconds
    - **Preloading**: Optionally preload a model on initialization

    Subclasses must implement:
    - _load_model(model_id): Load and return a model
    - _unload_model(model): Clean up a model

    Optionally override:
    - _cleanup_gpu_memory(): GPU memory cleanup after unload

    Args:
        ttl_seconds: Evict models idle longer than this (default: 3600)
        max_loaded: Maximum models to keep loaded (default: 2)
        preload: Model ID to preload on initialization (optional)
        eviction_check_interval: Seconds between TTL checks (default: 60)
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
        eviction_check_interval: int = 60,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_loaded = max_loaded
        self._eviction_check_interval = eviction_check_interval

        self._models: dict[str, LoadedModel[T]] = {}
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._eviction_thread: threading.Thread | None = None

        # Callbacks for monitoring (optional)
        self._on_load: Callable[[str], None] | None = None
        self._on_unload: Callable[[str, float], None] | None = None

        # Start background eviction thread
        self._start_eviction_thread()

        # Preload default model if specified
        if preload:
            logger.info("preloading_model", model_id=preload)
            self.acquire(preload)
            self.release(preload)

    def acquire(self, model_id: str) -> T:
        """Acquire a model for use, loading if necessary.

        This increments the reference count to prevent eviction during use.
        Caller MUST call release() when done.

        Args:
            model_id: Identifier of the model to acquire

        Returns:
            The loaded model object

        Raises:
            RuntimeError: If max_loaded models are in use and none can be evicted
            Exception: If model loading fails
        """
        with self._lock:
            if model_id not in self._models:
                self._maybe_evict_for_capacity()
                self._load_and_register(model_id)

            entry = self._models[model_id]
            entry.ref_count += 1
            entry.touch()

            logger.debug(
                "model_acquired",
                model_id=model_id,
                ref_count=entry.ref_count,
            )

            return entry.model

    def release(self, model_id: str) -> None:
        """Release a model reference.

        This decrements the reference count. If count reaches 0, the model
        becomes eligible for TTL eviction.

        Args:
            model_id: Identifier of the model to release
        """
        with self._lock:
            if model_id in self._models:
                entry = self._models[model_id]
                entry.ref_count = max(0, entry.ref_count - 1)

                logger.debug(
                    "model_released",
                    model_id=model_id,
                    ref_count=entry.ref_count,
                )

    def _load_and_register(self, model_id: str) -> None:
        """Load a model and register it. Called under lock."""
        start_time = time.time()
        logger.info("loading_model", model_id=model_id)

        model = self._load_model(model_id)
        load_time = time.time() - start_time

        self._models[model_id] = LoadedModel(
            model_id=model_id,
            model=model,
            loaded_at=time.time(),
            last_used_at=time.time(),
        )

        logger.info(
            "model_loaded",
            model_id=model_id,
            load_time_seconds=round(load_time, 2),
            loaded_count=len(self._models),
        )

        if self._on_load:
            self._on_load(model_id)

    def _maybe_evict_for_capacity(self) -> None:
        """Evict LRU model if at max capacity. Called under lock."""
        if len(self._models) < self.max_loaded:
            return

        # Find LRU model with ref_count=0
        candidates = [(mid, m) for mid, m in self._models.items() if m.ref_count == 0]

        if not candidates:
            in_use = [f"{mid}(refs={m.ref_count})" for mid, m in self._models.items()]
            raise RuntimeError(
                f"Cannot load model: {self.max_loaded} models in use, none idle. "
                f"In use: {', '.join(in_use)}"
            )

        # Evict least recently used
        lru_id, _ = min(candidates, key=lambda x: x[1].last_used_at)
        logger.info(
            "evicting_for_capacity",
            model_id=lru_id,
            max_loaded=self.max_loaded,
        )
        self._evict(lru_id)

    def _evict(self, model_id: str) -> None:
        """Evict a specific model. Called under lock."""
        entry = self._models.pop(model_id, None)
        if entry is None:
            return

        idle_seconds = entry.idle_seconds
        loaded_seconds = entry.loaded_seconds

        logger.info(
            "model_evicted",
            model_id=model_id,
            idle_seconds=round(idle_seconds, 1),
            loaded_seconds=round(loaded_seconds, 1),
        )

        # Unload the model
        self._unload_model(entry.model)

        # Callback for metrics
        if self._on_unload:
            self._on_unload(model_id, loaded_seconds)

        # Cleanup
        del entry
        gc.collect()
        self._cleanup_gpu_memory()

    def _start_eviction_thread(self) -> None:
        """Start background thread for TTL-based eviction."""

        def eviction_loop() -> None:
            while not self._shutdown.wait(timeout=self._eviction_check_interval):
                self._evict_expired()

        self._eviction_thread = threading.Thread(
            target=eviction_loop,
            daemon=True,
            name="model-eviction",
        )
        self._eviction_thread.start()

    def _evict_expired(self) -> None:
        """Evict models that have exceeded TTL."""
        with self._lock:
            now = time.time()
            expired = [
                mid
                for mid, m in self._models.items()
                if m.ref_count == 0 and (now - m.last_used_at) > self.ttl_seconds
            ]

            for model_id in expired:
                logger.info(
                    "evicting_expired",
                    model_id=model_id,
                    ttl_seconds=self.ttl_seconds,
                )
                self._evict(model_id)

    def shutdown(self) -> None:
        """Shutdown manager and unload all models.

        Call this during engine shutdown to ensure clean cleanup.
        """
        logger.info("model_manager_shutdown", loaded_count=len(self._models))

        self._shutdown.set()

        if self._eviction_thread and self._eviction_thread.is_alive():
            self._eviction_thread.join(timeout=5)

        with self._lock:
            for model_id in list(self._models.keys()):
                self._evict(model_id)

    def get_stats(self) -> dict:
        """Return current manager statistics.

        Returns:
            Dict with manager state for monitoring/debugging
        """
        with self._lock:
            return {
                "loaded_models": list(self._models.keys()),
                "model_count": len(self._models),
                "max_loaded": self.max_loaded,
                "ttl_seconds": self.ttl_seconds,
                "models": {
                    mid: {
                        "ref_count": m.ref_count,
                        "idle_seconds": round(m.idle_seconds, 1),
                        "loaded_seconds": round(m.loaded_seconds, 1),
                    }
                    for mid, m in self._models.items()
                },
            }

    def is_loaded(self, model_id: str) -> bool:
        """Check if a model is currently loaded."""
        with self._lock:
            return model_id in self._models

    def loaded_models(self) -> list[str]:
        """Return list of currently loaded model IDs."""
        with self._lock:
            return list(self._models.keys())

    @abstractmethod
    def _load_model(self, model_id: str) -> T:
        """Load a model. Implemented by subclasses.

        Args:
            model_id: Identifier of the model to load

        Returns:
            The loaded model object

        Raises:
            Exception: If model loading fails
        """
        raise NotImplementedError

    @abstractmethod
    def _unload_model(self, model: T) -> None:
        """Unload a model. Implemented by subclasses.

        Args:
            model: The model object to unload
        """
        raise NotImplementedError

    def _cleanup_gpu_memory(self) -> None:
        """Optional GPU memory cleanup after model unload.

        Override this in subclasses that use GPU models.
        Default implementation attempts PyTorch CUDA cleanup if available.
        """
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except ImportError:
            pass
