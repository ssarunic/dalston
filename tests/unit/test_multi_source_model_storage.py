"""Unit tests for MultiSourceModelStorage and related classes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dalston.engine_sdk.model_storage import (
    HFModelStorage,
    ModelNotFoundError,
    ModelSource,
    MultiSourceModelStorage,
    NGCModelStorage,
)


class TestModelSource:
    """Tests for ModelSource enum."""

    def test_values(self):
        assert ModelSource.S3 == "s3"
        assert ModelSource.HF == "hf"
        assert ModelSource.NGC == "ngc"
        assert ModelSource.AUTO == "auto"

    def test_from_string(self):
        assert ModelSource("s3") is ModelSource.S3
        assert ModelSource("hf") is ModelSource.HF
        assert ModelSource("auto") is ModelSource.AUTO

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            ModelSource("invalid")


class TestModelNotFoundError:
    """Tests for ModelNotFoundError exception."""

    def test_message(self):
        err = ModelNotFoundError("org/model", ["s3", "hf"])
        assert "org/model" in str(err)
        assert "s3" in str(err)
        assert "hf" in str(err)

    def test_attributes(self):
        err = ModelNotFoundError("org/model", ["s3"])
        assert err.model_id == "org/model"
        assert err.sources_tried == ["s3"]


class TestMultiSourceFromEnv:
    """Tests for MultiSourceModelStorage.from_env()."""

    def test_default_source_is_s3(self, monkeypatch):
        monkeypatch.delenv("DALSTON_MODEL_SOURCE", raising=False)
        monkeypatch.setenv("DALSTON_S3_BUCKET", "test-bucket")
        monkeypatch.delenv("NGC_API_KEY", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage.source is ModelSource.S3
        assert storage._s3 is not None

    def test_hf_source(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "hf")
        monkeypatch.delenv("DALSTON_S3_BUCKET", raising=False)
        monkeypatch.delenv("NGC_API_KEY", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage.source is ModelSource.HF
        assert storage._hf is not None

    def test_auto_source(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "auto")
        monkeypatch.setenv("DALSTON_S3_BUCKET", "test-bucket")
        monkeypatch.delenv("NGC_API_KEY", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage.source is ModelSource.AUTO
        assert storage._s3 is not None
        assert storage._hf is not None

    def test_ngc_backend_created_with_api_key(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "auto")
        monkeypatch.setenv("NGC_API_KEY", "test-key")
        monkeypatch.delenv("DALSTON_S3_BUCKET", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage._ngc is not None

    def test_ngc_backend_absent_without_api_key(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "auto")
        monkeypatch.delenv("NGC_API_KEY", raising=False)
        monkeypatch.delenv("DALSTON_S3_BUCKET", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage._ngc is None

    def test_invalid_source_falls_back_to_s3(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "bogus")
        monkeypatch.setenv("DALSTON_S3_BUCKET", "test-bucket")
        monkeypatch.delenv("NGC_API_KEY", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage.source is ModelSource.S3

    def test_no_s3_backend_without_bucket(self, monkeypatch):
        monkeypatch.setenv("DALSTON_MODEL_SOURCE", "auto")
        monkeypatch.delenv("DALSTON_S3_BUCKET", raising=False)
        monkeypatch.delenv("NGC_API_KEY", raising=False)

        storage = MultiSourceModelStorage.from_env()
        assert storage._s3 is None


def _mock_backend(return_path: Path | None = None, *, side_effect=None):
    backend = MagicMock()
    if side_effect:
        backend.ensure_local.side_effect = side_effect
    else:
        backend.ensure_local.return_value = return_path
    return backend


class TestMultiSourceEnsureLocal:
    """Tests for ensure_local dispatch and auto fallback."""

    def test_s3_mode_dispatches_to_s3(self):
        s3 = _mock_backend(Path("/cache/s3/model"))
        storage = MultiSourceModelStorage(ModelSource.S3, s3=s3)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/s3/model")
        s3.ensure_local.assert_called_once_with("org/model")

    def test_s3_mode_without_bucket_raises(self):
        storage = MultiSourceModelStorage(ModelSource.S3)
        with pytest.raises(ValueError, match="DALSTON_S3_BUCKET"):
            storage.ensure_local("org/model")

    def test_hf_mode_dispatches_to_hf(self):
        hf = _mock_backend(Path("/cache/hf/model"))
        storage = MultiSourceModelStorage(ModelSource.HF, hf=hf)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/hf/model")
        hf.ensure_local.assert_called_once_with("org/model")

    def test_ngc_mode_without_key_raises(self):
        storage = MultiSourceModelStorage(ModelSource.NGC)
        with pytest.raises(ValueError, match="NGC_API_KEY"):
            storage.ensure_local("org/model")

    def test_auto_tries_s3_first(self):
        s3 = _mock_backend(Path("/cache/s3/model"))
        hf = _mock_backend(Path("/cache/hf/model"))
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/s3/model")
        s3.ensure_local.assert_called_once()
        hf.ensure_local.assert_not_called()

    def test_auto_falls_back_to_hf_on_s3_failure(self):
        s3 = _mock_backend(side_effect=Exception("S3 unavailable"))
        hf = _mock_backend(Path("/cache/hf/model"))
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/hf/model")
        s3.ensure_local.assert_called_once()
        hf.ensure_local.assert_called_once()

    def test_auto_falls_back_to_ngc_on_s3_and_hf_failure(self):
        s3 = _mock_backend(side_effect=Exception("S3 down"))
        hf = _mock_backend(side_effect=Exception("HF down"))
        ngc = _mock_backend(Path("/cache/ngc/model"))
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf, ngc=ngc)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/ngc/model")

    def test_auto_raises_model_not_found_when_all_fail(self):
        s3 = _mock_backend(side_effect=Exception("S3 down"))
        hf = _mock_backend(side_effect=Exception("HF down"))
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        with pytest.raises(ModelNotFoundError) as exc_info:
            storage.ensure_local("org/model")
        assert exc_info.value.model_id == "org/model"
        assert "s3" in exc_info.value.sources_tried
        assert "hf" in exc_info.value.sources_tried

    def test_auto_skips_unconfigured_backends(self):
        hf = _mock_backend(Path("/cache/hf/model"))
        storage = MultiSourceModelStorage(ModelSource.AUTO, hf=hf)

        result = storage.ensure_local("org/model")
        assert result == Path("/cache/hf/model")

    def test_auto_no_backends_raises_empty_sources(self):
        storage = MultiSourceModelStorage(ModelSource.AUTO)

        with pytest.raises(ModelNotFoundError) as exc_info:
            storage.ensure_local("org/model")
        assert exc_info.value.sources_tried == []


class TestMultiSourceIsCachedLocally:
    """Tests for is_cached_locally across backends."""

    def test_returns_true_if_s3_cached(self):
        s3 = MagicMock()
        s3.is_cached_locally.return_value = True
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3)

        assert storage.is_cached_locally("org/model") is True

    def test_returns_true_if_hf_cached(self):
        s3 = MagicMock()
        s3.is_cached_locally.return_value = False
        hf = MagicMock()
        hf.is_cached_locally.return_value = True
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        assert storage.is_cached_locally("org/model") is True

    def test_returns_false_if_none_cached(self):
        s3 = MagicMock()
        s3.is_cached_locally.return_value = False
        hf = MagicMock()
        hf.is_cached_locally.return_value = False
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        assert storage.is_cached_locally("org/model") is False

    def test_returns_false_with_no_backends(self):
        storage = MultiSourceModelStorage(ModelSource.AUTO)
        assert storage.is_cached_locally("org/model") is False


class TestMultiSourceGetCacheStats:
    """Tests for get_cache_stats backend selection."""

    def test_s3_mode_returns_s3_stats(self):
        s3 = MagicMock()
        s3.get_cache_stats.return_value = {"source": "s3", "model_count": 3}
        storage = MultiSourceModelStorage(ModelSource.S3, s3=s3)

        stats = storage.get_cache_stats()
        assert stats["source"] == "s3"
        assert stats["model_count"] == 3

    def test_hf_mode_returns_hf_stats(self):
        hf = MagicMock()
        hf.get_cache_stats.return_value = {"source": "hf", "model_count": 1}
        storage = MultiSourceModelStorage(ModelSource.HF, hf=hf)

        stats = storage.get_cache_stats()
        assert stats["source"] == "hf"

    def test_auto_mode_prefers_s3_stats(self):
        s3 = MagicMock()
        s3.get_cache_stats.return_value = {"source": "s3", "model_count": 2}
        hf = MagicMock()
        hf.get_cache_stats.return_value = {"source": "hf", "model_count": 5}
        storage = MultiSourceModelStorage(ModelSource.AUTO, s3=s3, hf=hf)

        stats = storage.get_cache_stats()
        assert stats["source"] == "s3"

    def test_auto_mode_falls_back_to_hf_stats(self):
        hf = MagicMock()
        hf.get_cache_stats.return_value = {"source": "hf", "model_count": 1}
        storage = MultiSourceModelStorage(ModelSource.AUTO, hf=hf)

        stats = storage.get_cache_stats()
        assert stats["source"] == "hf"

    def test_no_backends_returns_empty_stats(self):
        storage = MultiSourceModelStorage(ModelSource.AUTO)
        stats = storage.get_cache_stats()
        assert stats["model_count"] == 0


class TestHFModelStorage:
    """Tests for HFModelStorage."""

    def test_ensure_local_calls_snapshot_download(self):
        with patch(
            "huggingface_hub.snapshot_download",
            return_value="/cache/hf/snapshots/abc123",
        ) as mock_dl:
            storage = HFModelStorage(token="hf_test")
            result = storage.ensure_local("Systran/faster-whisper-base")

            mock_dl.assert_called_once_with(
                "Systran/faster-whisper-base", token="hf_test"
            )
            assert result == Path("/cache/hf/snapshots/abc123")

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_env_token")
        storage = HFModelStorage()
        assert storage.token == "hf_env_token"

    def test_token_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_env_token")
        storage = HFModelStorage(token="hf_explicit")
        assert storage.token == "hf_explicit"

    def test_is_cached_locally_delegates(self):
        with patch(
            "dalston.engine_sdk.model_storage.is_model_cached", return_value=True
        ) as mock_cached:
            storage = HFModelStorage()
            assert storage.is_cached_locally("org/model") is True
            mock_cached.assert_called_once_with("org/model", framework="huggingface")

    def test_get_cache_stats_returns_hf_source(self):
        storage = HFModelStorage()
        stats = storage.get_cache_stats()
        assert stats["source"] == "hf"
        assert stats["model_count"] == 0


class TestNGCModelStorage:
    """Tests for NGCModelStorage stub."""

    def test_ensure_local_raises_not_implemented(self):
        storage = NGCModelStorage(api_key="test-key")
        with pytest.raises(NotImplementedError, match="NGC model download"):
            storage.ensure_local("nvidia/parakeet-tdt-0.6b-v2")

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("NGC_API_KEY", "ngc_env_key")
        storage = NGCModelStorage()
        assert storage.api_key == "ngc_env_key"

    def test_is_cached_locally_delegates(self):
        with patch(
            "dalston.engine_sdk.model_storage.is_model_cached", return_value=False
        ) as mock_cached:
            storage = NGCModelStorage()
            assert storage.is_cached_locally("nvidia/model") is False
            mock_cached.assert_called_once_with("nvidia/model", framework="nemo")

    def test_get_cache_stats_returns_ngc_source(self):
        storage = NGCModelStorage()
        stats = storage.get_cache_stats()
        assert stats["source"] == "ngc"
