"""Unit tests for NeMo model managers (M44).

Tests the NeMoModelManager and OnnxModelManager classes
for dynamic model loading.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestNeMoModelManagerInit:
    """Tests for NeMoModelManager initialization."""

    def test_init_with_defaults(self):
        """Test manager creation with default settings."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.nemo import NeMoModelManager

            manager = NeMoModelManager(
                device="cuda",
                ttl_seconds=3600,
                max_loaded=2,
            )

            assert manager.device == "cuda"
            assert manager.ttl_seconds == 3600
            assert manager.max_loaded == 2

            manager.shutdown()

    def test_supported_models(self):
        """Test that supported models are properly defined."""
        from dalston.engine_sdk.managers.nemo import NeMoModelManager

        assert "parakeet-rnnt-0.6b" in NeMoModelManager.SUPPORTED_MODELS
        assert "parakeet-rnnt-1.1b" in NeMoModelManager.SUPPORTED_MODELS
        assert "parakeet-ctc-0.6b" in NeMoModelManager.SUPPORTED_MODELS
        assert "parakeet-ctc-1.1b" in NeMoModelManager.SUPPORTED_MODELS
        assert "parakeet-tdt-0.6b-v3" in NeMoModelManager.SUPPORTED_MODELS
        assert "parakeet-tdt-1.1b" in NeMoModelManager.SUPPORTED_MODELS

    def test_architecture_detection(self):
        """Test architecture detection from model ID."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.nemo import NeMoModelManager

            manager = NeMoModelManager(device="cpu")

            assert manager.get_architecture("parakeet-rnnt-0.6b") == "rnnt"
            assert manager.get_architecture("parakeet-ctc-1.1b") == "ctc"
            assert manager.get_architecture("parakeet-tdt-0.6b-v3") == "tdt"
            assert manager.get_architecture("parakeet-tdt-1.1b") == "tdt"

            manager.shutdown()

    def test_from_env_defaults(self):
        """Test from_env with no environment variables set."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            with patch.dict("os.environ", {}, clear=True):
                with patch.dict("sys.modules", {"torch": mock_torch}):
                    from dalston.engine_sdk.managers.nemo import NeMoModelManager

                    manager = NeMoModelManager.from_env()

                    assert manager.device == "cpu"
                    assert manager.ttl_seconds == 3600
                    assert manager.max_loaded == 2

                    manager.shutdown()

    def test_from_env_with_cuda(self):
        """Test from_env when CUDA is available."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            with patch.dict("os.environ", {}, clear=True):
                with patch.dict("sys.modules", {"torch": mock_torch}):
                    from dalston.engine_sdk.managers.nemo import NeMoModelManager

                    manager = NeMoModelManager.from_env()

                    assert manager.device == "cuda"

                    manager.shutdown()

    def test_from_env_with_custom_settings(self):
        """Test from_env with custom environment variables."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            env_vars = {
                "DALSTON_DEVICE": "cpu",
                "DALSTON_MODEL_TTL_SECONDS": "7200",
                "DALSTON_MAX_LOADED_MODELS": "3",
            }
            with patch.dict("os.environ", env_vars, clear=True):
                from dalston.engine_sdk.managers.nemo import NeMoModelManager

                manager = NeMoModelManager.from_env()

                assert manager.device == "cpu"
                assert manager.ttl_seconds == 7200
                assert manager.max_loaded == 3

                manager.shutdown()

    def test_load_model_invalid_id(self):
        """Test loading invalid model ID raises ValueError."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.nemo import NeMoModelManager

            manager = NeMoModelManager(device="cpu")

            with pytest.raises(ValueError, match="Unknown model"):
                manager._load_model("invalid-model")

            manager.shutdown()


class TestOnnxModelManagerInit:
    """Tests for OnnxModelManager initialization."""

    def test_init_with_defaults(self):
        """Test manager creation with default settings."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(
                device="cpu",
                quantization="none",
                ttl_seconds=3600,
                max_loaded=2,
            )

            assert manager.device == "cpu"
            assert manager.quantization is None
            assert manager.ttl_seconds == 3600
            assert manager.max_loaded == 2
            assert "CPUExecutionProvider" in manager._providers

            manager.shutdown()

    def test_init_with_cuda(self):
        """Test manager creation with CUDA device."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(
                device="cuda",
                ttl_seconds=3600,
                max_loaded=2,
            )

            assert manager.device == "cuda"
            assert "CUDAExecutionProvider" in manager._providers
            assert "CPUExecutionProvider" in manager._providers

            manager.shutdown()

    def test_init_with_quantization(self):
        """Test manager creation with quantization enabled."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(
                device="cpu",
                quantization="int8",
                ttl_seconds=3600,
                max_loaded=2,
            )

            assert manager.quantization == "int8"

            manager.shutdown()

    def test_model_aliases(self):
        """Test that model aliases are properly defined."""
        from dalston.engine_sdk.managers.onnx import OnnxModelManager

        # Full names
        assert "parakeet-onnx-ctc-0.6b" in OnnxModelManager.MODEL_ALIASES
        assert "parakeet-onnx-ctc-1.1b" in OnnxModelManager.MODEL_ALIASES
        assert "parakeet-onnx-tdt-0.6b-v2" in OnnxModelManager.MODEL_ALIASES
        assert "parakeet-onnx-tdt-0.6b-v3" in OnnxModelManager.MODEL_ALIASES
        assert "parakeet-onnx-rnnt-0.6b" in OnnxModelManager.MODEL_ALIASES

        # Short aliases
        assert "ctc-0.6b" in OnnxModelManager.MODEL_ALIASES
        assert "tdt-0.6b-v3" in OnnxModelManager.MODEL_ALIASES

    def test_from_env_defaults(self):
        """Test from_env with no environment variables set."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            with patch.dict("os.environ", {}, clear=True):
                # Mock onnxruntime to control device detection
                mock_ort = MagicMock()
                mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]

                with patch.dict("sys.modules", {"onnxruntime": mock_ort}):
                    from dalston.engine_sdk.managers.onnx import (
                        OnnxModelManager,
                    )

                    manager = OnnxModelManager.from_env()

                    assert manager.device == "cpu"
                    assert manager.ttl_seconds == 3600
                    assert manager.max_loaded == 2

                    manager.shutdown()

    def test_from_env_with_custom_settings(self):
        """Test from_env with custom environment variables."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            env_vars = {
                "DALSTON_DEVICE": "cpu",
                "DALSTON_QUANTIZATION": "int8",
                "DALSTON_MODEL_TTL_SECONDS": "1800",
                "DALSTON_MAX_LOADED_MODELS": "4",
            }
            with patch.dict("os.environ", env_vars, clear=True):
                from dalston.engine_sdk.managers.onnx import OnnxModelManager

                manager = OnnxModelManager.from_env()

                assert manager.device == "cpu"
                assert manager.quantization == "int8"
                assert manager.ttl_seconds == 1800
                assert manager.max_loaded == 4

                manager.shutdown()

    def test_load_model_passthrough_unknown_id(self):
        """Test that unknown model IDs are passed through to onnx_asr."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(device="cpu")

            mock_model = MagicMock()
            mock_onnx_asr = MagicMock()
            mock_onnx_asr.load_model.return_value = mock_model

            with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
                result = manager._load_model("openai/whisper-large-v3")

            # Unknown ID passed through as-is to onnx_asr.load_model()
            mock_onnx_asr.load_model.assert_called_once_with(
                "openai/whisper-large-v3",
                quantization=None,
                providers=["CPUExecutionProvider"],
            )
            assert result is mock_model

            manager.shutdown()


class TestNeMoModelManagerModelLoading:
    """Tests for NeMoModelManager model loading mechanics."""

    def test_model_acquire_release_flow(self):
        """Test the acquire/release flow without actual model loading."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.nemo import NeMoModelManager

            manager = NeMoModelManager(device="cpu")

            # Mock the _load_model method
            mock_model = MagicMock()
            manager._load_model = MagicMock(return_value=mock_model)

            # Acquire model
            model = manager.acquire("parakeet-rnnt-0.6b")

            assert model is mock_model
            assert manager.is_loaded("parakeet-rnnt-0.6b")
            manager._load_model.assert_called_once_with("parakeet-rnnt-0.6b")

            # Check ref count
            stats = manager.get_stats()
            assert stats["models"]["parakeet-rnnt-0.6b"]["ref_count"] == 1

            # Release model
            manager.release("parakeet-rnnt-0.6b")
            stats = manager.get_stats()
            assert stats["models"]["parakeet-rnnt-0.6b"]["ref_count"] == 0

            manager.shutdown()

    def test_model_reuse(self):
        """Test that acquiring the same model twice reuses the loaded instance."""
        with patch(
            "dalston.engine_sdk.managers.nemo.NeMoModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.nemo import NeMoModelManager

            manager = NeMoModelManager(device="cpu")

            mock_model = MagicMock()
            manager._load_model = MagicMock(return_value=mock_model)

            model1 = manager.acquire("parakeet-rnnt-0.6b")
            model2 = manager.acquire("parakeet-rnnt-0.6b")

            assert model1 is model2
            # Should only load once
            assert manager._load_model.call_count == 1

            # Ref count should be 2
            stats = manager.get_stats()
            assert stats["models"]["parakeet-rnnt-0.6b"]["ref_count"] == 2

            manager.shutdown()


class TestOnnxModelManagerModelLoading:
    """Tests for OnnxModelManager model loading mechanics."""

    def test_model_acquire_release_flow(self):
        """Test the acquire/release flow without actual model loading."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(device="cpu")

            # Mock the _load_model method
            mock_model = MagicMock()
            manager._load_model = MagicMock(return_value=mock_model)

            # Acquire model
            model = manager.acquire("parakeet-onnx-ctc-0.6b")

            assert model is mock_model
            assert manager.is_loaded("parakeet-onnx-ctc-0.6b")
            manager._load_model.assert_called_once_with("parakeet-onnx-ctc-0.6b")

            # Check ref count
            stats = manager.get_stats()
            assert stats["models"]["parakeet-onnx-ctc-0.6b"]["ref_count"] == 1

            # Release model
            manager.release("parakeet-onnx-ctc-0.6b")
            stats = manager.get_stats()
            assert stats["models"]["parakeet-onnx-ctc-0.6b"]["ref_count"] == 0

            manager.shutdown()

    def test_short_alias_model_loading(self):
        """Test loading model using short alias."""
        with patch(
            "dalston.engine_sdk.managers.onnx.OnnxModelManager._start_eviction_thread"
        ):
            from dalston.engine_sdk.managers.onnx import OnnxModelManager

            manager = OnnxModelManager(device="cpu")

            # Mock the _load_model method but capture what's called
            mock_model = MagicMock()
            original_load = manager._load_model

            def mock_load(model_id):
                # Verify the model ID is resolved correctly
                if model_id in OnnxModelManager.MODEL_ALIASES:
                    return mock_model
                return original_load(model_id)

            manager._load_model = mock_load

            # Use short alias
            model = manager.acquire("ctc-0.6b")
            assert model is mock_model
            assert manager.is_loaded("ctc-0.6b")

            manager.shutdown()


class TestModuleExports:
    """Tests for proper module exports."""

    def test_managers_init_exports(self):
        """Test that managers/__init__.py exports all managers."""
        from dalston.engine_sdk.managers import (
            FasterWhisperModelManager,
            HFTransformersModelManager,
            NeMoModelManager,
            OnnxModelManager,
        )

        # Just verify they're importable
        assert FasterWhisperModelManager is not None
        assert HFTransformersModelManager is not None
        assert NeMoModelManager is not None
        assert OnnxModelManager is not None
