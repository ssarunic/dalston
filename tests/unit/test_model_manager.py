"""Unit tests for TTL-based model manager.

Tests the ModelManager base class and its eviction behavior.
"""

import threading
import time

import pytest

from dalston.engine_sdk.model_manager import LoadedModel, ModelManager


class MockModel:
    """Mock model for testing."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.unloaded = False


class MockModelManager(ModelManager[MockModel]):
    """Concrete implementation for testing."""

    def __init__(self, load_delay: float = 0, **kwargs):
        self.load_delay = load_delay
        self.load_count = 0
        self.unload_count = 0
        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> MockModel:
        if self.load_delay:
            time.sleep(self.load_delay)
        self.load_count += 1
        return MockModel(model_id)

    def _unload_model(self, model: MockModel) -> None:
        model.unloaded = True
        self.unload_count += 1


class TestLoadedModel:
    """Tests for LoadedModel dataclass."""

    def test_create_loaded_model(self):
        """Test creating a LoadedModel."""
        now = time.time()
        model = MockModel("test")
        loaded = LoadedModel(
            model_id="test",
            model=model,
            loaded_at=now,
            last_used_at=now,
        )

        assert loaded.model_id == "test"
        assert loaded.model is model
        assert loaded.ref_count == 0
        assert loaded.size_bytes is None

    def test_touch_updates_last_used(self):
        """Test that touch() updates last_used_at."""
        now = time.time()
        loaded = LoadedModel(
            model_id="test",
            model=MockModel("test"),
            loaded_at=now,
            last_used_at=now,
        )

        time.sleep(0.01)
        loaded.touch()

        assert loaded.last_used_at > now

    def test_idle_seconds(self):
        """Test idle_seconds property."""
        loaded = LoadedModel(
            model_id="test",
            model=MockModel("test"),
            loaded_at=time.time(),
            last_used_at=time.time() - 10,
        )

        assert loaded.idle_seconds >= 10

    def test_loaded_seconds(self):
        """Test loaded_seconds property."""
        loaded = LoadedModel(
            model_id="test",
            model=MockModel("test"),
            loaded_at=time.time() - 60,
            last_used_at=time.time(),
        )

        assert loaded.loaded_seconds >= 60


class TestModelManagerBasics:
    """Tests for basic ModelManager functionality."""

    def test_acquire_loads_model(self):
        """Test that acquire() loads a model."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        model = manager.acquire("test-model")

        assert model is not None
        assert model.model_id == "test-model"
        assert manager.load_count == 1
        assert manager.is_loaded("test-model")

        manager.shutdown()

    def test_acquire_same_model_twice(self):
        """Test that acquiring same model twice doesn't reload."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        model1 = manager.acquire("test-model")
        model2 = manager.acquire("test-model")

        assert model1 is model2
        assert manager.load_count == 1

        manager.shutdown()

    def test_acquire_increments_ref_count(self):
        """Test that acquire() increments reference count."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        manager.acquire("test-model")
        manager.acquire("test-model")

        stats = manager.get_stats()
        assert stats["models"]["test-model"]["ref_count"] == 2

        manager.shutdown()

    def test_release_decrements_ref_count(self):
        """Test that release() decrements reference count."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        manager.acquire("test-model")
        manager.acquire("test-model")
        manager.release("test-model")

        stats = manager.get_stats()
        assert stats["models"]["test-model"]["ref_count"] == 1

        manager.shutdown()

    def test_release_clamps_at_zero(self):
        """Test that release() doesn't go below zero."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        manager.acquire("test-model")
        manager.release("test-model")
        manager.release("test-model")
        manager.release("test-model")

        stats = manager.get_stats()
        assert stats["models"]["test-model"]["ref_count"] == 0

        manager.shutdown()

    def test_release_nonexistent_model(self):
        """Test that releasing a non-loaded model is safe."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        # Should not raise
        manager.release("nonexistent")

        manager.shutdown()


class TestModelManagerLRUEviction:
    """Tests for LRU eviction when at capacity."""

    def test_evicts_lru_when_at_capacity(self):
        """Test that LRU model is evicted when max_loaded is reached."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        # Load two models
        manager.acquire("model-1")
        manager.release("model-1")
        time.sleep(0.01)  # Ensure different timestamps

        manager.acquire("model-2")
        manager.release("model-2")
        time.sleep(0.01)

        # Load third model - should evict model-1 (LRU)
        manager.acquire("model-3")
        manager.release("model-3")

        assert not manager.is_loaded("model-1")
        assert manager.is_loaded("model-2")
        assert manager.is_loaded("model-3")
        assert manager.unload_count == 1

        manager.shutdown()

    def test_does_not_evict_in_use_models(self):
        """Test that models with ref_count > 0 are not evicted."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        # Load two models, keep model-1 in use
        manager.acquire("model-1")  # ref_count = 1, NOT released
        time.sleep(0.01)

        manager.acquire("model-2")
        manager.release("model-2")
        time.sleep(0.01)

        # Load third model - should evict model-2 (only idle one)
        manager.acquire("model-3")
        manager.release("model-3")

        assert manager.is_loaded("model-1")  # Still in use
        assert not manager.is_loaded("model-2")  # Evicted
        assert manager.is_loaded("model-3")

        manager.shutdown()

    def test_raises_when_all_models_in_use(self):
        """Test that RuntimeError is raised when all models are in use."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        # Load two models and don't release
        manager.acquire("model-1")
        manager.acquire("model-2")

        # Try to load third model - should fail
        with pytest.raises(RuntimeError, match="Cannot load model"):
            manager.acquire("model-3")

        manager.shutdown()


class TestModelManagerTTLEviction:
    """Tests for TTL-based eviction."""

    def test_evicts_expired_models(self):
        """Test that models idle longer than TTL are evicted."""
        # Use very short TTL and check interval for testing
        manager = MockModelManager(
            ttl_seconds=1,  # 1 second TTL
            max_loaded=5,
            eviction_check_interval=1,  # Check every second
        )

        manager.acquire("test-model")
        manager.release("test-model")

        # Wait for TTL to expire and eviction to run
        time.sleep(2.5)

        assert not manager.is_loaded("test-model")
        assert manager.unload_count == 1

        manager.shutdown()

    def test_does_not_evict_recently_used(self):
        """Test that recently used models are not evicted."""
        manager = MockModelManager(
            ttl_seconds=2,
            max_loaded=5,
            eviction_check_interval=1,
        )

        manager.acquire("test-model")
        manager.release("test-model")

        # Touch the model before TTL expires
        time.sleep(1)
        manager.acquire("test-model")
        manager.release("test-model")

        # Wait a bit more
        time.sleep(1)

        # Model should still be loaded (TTL reset)
        assert manager.is_loaded("test-model")

        manager.shutdown()

    def test_does_not_evict_in_use_even_after_ttl(self):
        """Test that in-use models are not evicted even after TTL."""
        manager = MockModelManager(
            ttl_seconds=1,
            max_loaded=5,
            eviction_check_interval=1,
        )

        manager.acquire("test-model")  # Don't release

        # Wait for TTL to expire
        time.sleep(2.5)

        # Model should still be loaded (in use)
        assert manager.is_loaded("test-model")
        assert manager.unload_count == 0

        manager.shutdown()


class TestModelManagerPreload:
    """Tests for model preloading."""

    def test_preloads_model_on_init(self):
        """Test that preload parameter loads model on init."""
        manager = MockModelManager(
            ttl_seconds=3600,
            max_loaded=2,
            preload="preloaded-model",
        )

        assert manager.is_loaded("preloaded-model")
        assert manager.load_count == 1

        # Model should be released after preload
        stats = manager.get_stats()
        assert stats["models"]["preloaded-model"]["ref_count"] == 0

        manager.shutdown()


class TestModelManagerStats:
    """Tests for get_stats() method."""

    def test_get_stats_empty(self):
        """Test get_stats() with no models loaded."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        stats = manager.get_stats()

        assert stats["loaded_models"] == []
        assert stats["model_count"] == 0
        assert stats["max_loaded"] == 2
        assert stats["ttl_seconds"] == 3600

        manager.shutdown()

    def test_get_stats_with_models(self):
        """Test get_stats() with loaded models."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=5)

        manager.acquire("model-1")
        manager.acquire("model-2")
        manager.release("model-2")

        stats = manager.get_stats()

        assert set(stats["loaded_models"]) == {"model-1", "model-2"}
        assert stats["model_count"] == 2
        assert stats["models"]["model-1"]["ref_count"] == 1
        assert stats["models"]["model-2"]["ref_count"] == 0

        manager.shutdown()


class TestModelManagerShutdown:
    """Tests for shutdown() method."""

    def test_shutdown_unloads_all_models(self):
        """Test that shutdown() unloads all models."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=5)

        manager.acquire("model-1")
        manager.acquire("model-2")
        manager.release("model-1")
        manager.release("model-2")

        manager.shutdown()

        assert manager.unload_count == 2
        assert not manager.is_loaded("model-1")
        assert not manager.is_loaded("model-2")

    def test_shutdown_stops_eviction_thread(self):
        """Test that shutdown() stops the eviction thread."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        assert manager._eviction_thread is not None
        assert manager._eviction_thread.is_alive()

        manager.shutdown()

        # Give thread time to stop
        time.sleep(0.1)
        assert not manager._eviction_thread.is_alive()


class TestModelManagerThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_acquire_release(self):
        """Test concurrent acquire/release operations."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=10)
        errors = []

        def worker(model_id: str, iterations: int):
            try:
                for _ in range(iterations):
                    model = manager.acquire(model_id)
                    assert model is not None
                    time.sleep(0.001)
                    manager.release(model_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"model-{i}", 10)) for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        manager.shutdown()

    def test_concurrent_acquire_same_model(self):
        """Test concurrent acquire of the same model."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=5)
        acquired_models = []
        lock = threading.Lock()

        def worker():
            model = manager.acquire("shared-model")
            with lock:
                acquired_models.append(model)
            time.sleep(0.01)
            manager.release("shared-model")

        threads = [threading.Thread(target=worker) for _ in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should have received the same model instance
        assert len({id(m) for m in acquired_models}) == 1
        # Model should only have been loaded once
        assert manager.load_count == 1

        manager.shutdown()


class TestModelManagerLoadedModels:
    """Tests for loaded_models() method."""

    def test_loaded_models_empty(self):
        """Test loaded_models() with no models."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=2)

        assert manager.loaded_models() == []

        manager.shutdown()

    def test_loaded_models_returns_list(self):
        """Test loaded_models() returns list of model IDs."""
        manager = MockModelManager(ttl_seconds=3600, max_loaded=5)

        manager.acquire("model-a")
        manager.acquire("model-b")
        manager.release("model-a")
        manager.release("model-b")

        models = manager.loaded_models()

        assert set(models) == {"model-a", "model-b"}

        manager.shutdown()
