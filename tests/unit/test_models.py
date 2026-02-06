"""Unit tests for model selection registry."""

import pytest

from dalston.common.models import (
    DEFAULT_MODEL,
    MODEL_ALIASES,
    MODEL_REGISTRY,
    ModelDefinition,
    get_available_models,
    get_model_ids,
    resolve_model,
)


class TestModelRegistry:
    """Tests for the model registry."""

    def test_default_model_exists(self):
        """Test that the default model is registered."""
        assert DEFAULT_MODEL in MODEL_REGISTRY

    def test_default_model_is_whisper_large_v3(self):
        """Test that the default model is whisper-large-v3."""
        assert DEFAULT_MODEL == "whisper-large-v3"

    def test_registry_contains_expected_models(self):
        """Test that all expected Whisper models are registered."""
        expected_models = [
            "whisper-large-v3",
            "whisper-large-v2",
            "whisper-medium",
            "whisper-small",
            "whisper-base",
            "whisper-tiny",
            "distil-whisper",
        ]
        for model_id in expected_models:
            assert model_id in MODEL_REGISTRY, f"Missing model: {model_id}"

    def test_all_models_have_required_fields(self):
        """Test that all models have required fields populated."""
        for model_id, model in MODEL_REGISTRY.items():
            assert isinstance(model, ModelDefinition)
            assert model.id == model_id
            assert model.engine
            assert model.engine_model
            assert model.name
            assert model.description
            assert model.tier in ("fast", "balanced", "accurate")
            assert model.languages >= 1
            assert isinstance(model.streaming, bool)
            assert isinstance(model.word_timestamps, bool)
            assert model.vram_gb > 0
            assert model.speed_factor > 0


class TestModelAliases:
    """Tests for model aliases."""

    def test_fast_alias(self):
        """Test that 'fast' alias points to distil-whisper."""
        assert MODEL_ALIASES["fast"] == "distil-whisper"

    def test_accurate_alias(self):
        """Test that 'accurate' alias points to whisper-large-v3."""
        assert MODEL_ALIASES["accurate"] == "whisper-large-v3"

    def test_large_alias(self):
        """Test that 'large' alias points to whisper-large-v3."""
        assert MODEL_ALIASES["large"] == "whisper-large-v3"

    def test_all_aliases_resolve_to_valid_models(self):
        """Test that all aliases point to valid models."""
        for alias, model_id in MODEL_ALIASES.items():
            assert (
                model_id in MODEL_REGISTRY
            ), f"Alias '{alias}' points to unknown model '{model_id}'"


class TestResolveModel:
    """Tests for resolve_model function."""

    def test_resolve_exact_model_id(self):
        """Test resolving a model by exact ID."""
        model = resolve_model("whisper-large-v3")
        assert model.id == "whisper-large-v3"
        assert model.engine == "faster-whisper"
        assert model.engine_model == "large-v3"

    def test_resolve_alias(self):
        """Test resolving a model by alias."""
        model = resolve_model("fast")
        assert model.id == "distil-whisper"
        assert model.engine == "faster-whisper"

    def test_resolve_accurate_alias(self):
        """Test resolving the 'accurate' alias."""
        model = resolve_model("accurate")
        assert model.id == "whisper-large-v3"

    def test_resolve_unknown_model_raises(self):
        """Test that resolving an unknown model raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            resolve_model("nonexistent-model")

        assert "Unknown model" in str(exc_info.value)
        assert "nonexistent-model" in str(exc_info.value)
        assert "Available models" in str(exc_info.value)

    def test_resolve_all_registered_models(self):
        """Test that all registered models can be resolved."""
        for model_id in MODEL_REGISTRY:
            model = resolve_model(model_id)
            assert model.id == model_id

    def test_resolve_all_aliases(self):
        """Test that all aliases can be resolved."""
        for alias in MODEL_ALIASES:
            model = resolve_model(alias)
            assert model is not None
            assert model.id in MODEL_REGISTRY


class TestGetAvailableModels:
    """Tests for get_available_models function."""

    def test_returns_list(self):
        """Test that get_available_models returns a list."""
        models = get_available_models()
        assert isinstance(models, list)

    def test_returns_all_registered_models(self):
        """Test that all registered models are returned."""
        models = get_available_models()
        assert len(models) == len(MODEL_REGISTRY)

    def test_returns_model_definitions(self):
        """Test that all items are ModelDefinition instances."""
        models = get_available_models()
        for model in models:
            assert isinstance(model, ModelDefinition)


class TestGetModelIds:
    """Tests for get_model_ids function."""

    def test_returns_list(self):
        """Test that get_model_ids returns a list."""
        model_ids = get_model_ids()
        assert isinstance(model_ids, list)

    def test_returns_all_model_ids(self):
        """Test that all model IDs are returned."""
        model_ids = get_model_ids()
        assert len(model_ids) == len(MODEL_REGISTRY)

    def test_does_not_include_aliases(self):
        """Test that aliases are not included in model IDs."""
        model_ids = get_model_ids()
        for alias in MODEL_ALIASES:
            # Aliases should only appear if they're also a model ID
            if alias in model_ids:
                assert alias in MODEL_REGISTRY


class TestModelDefinitionFields:
    """Tests for specific model field values."""

    def test_whisper_large_v3_config(self):
        """Test whisper-large-v3 model configuration."""
        model = MODEL_REGISTRY["whisper-large-v3"]
        assert model.engine == "faster-whisper"
        assert model.engine_model == "large-v3"
        assert model.tier == "accurate"
        assert model.languages == 99
        assert model.word_timestamps is True
        assert model.vram_gb == 10.0

    def test_distil_whisper_english_only(self):
        """Test that distil-whisper is English-only."""
        model = MODEL_REGISTRY["distil-whisper"]
        assert model.languages == 1  # English only
        assert model.tier == "fast"

    def test_model_speed_factors_ordered(self):
        """Test that smaller models have higher speed factors."""
        large = MODEL_REGISTRY["whisper-large-v3"]
        medium = MODEL_REGISTRY["whisper-medium"]
        small = MODEL_REGISTRY["whisper-small"]
        base = MODEL_REGISTRY["whisper-base"]
        tiny = MODEL_REGISTRY["whisper-tiny"]

        # Smaller models should be faster (higher speed_factor)
        assert tiny.speed_factor > base.speed_factor
        assert base.speed_factor > small.speed_factor
        assert small.speed_factor > medium.speed_factor
        assert medium.speed_factor > large.speed_factor
