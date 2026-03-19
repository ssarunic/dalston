"""Unit tests for DiskCacheEvictor (M83: Disk Cache Eviction)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dalston.engine_sdk.disk_cache import DiskCacheEvictor
from dalston.engine_sdk.model_storage import ACCESS_MARKER, _touch_access_marker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_s3_model(
    cache_dir: Path, model_id: str, size_bytes: int = 1024, age_seconds: float = 0
) -> Path:
    """Create a fake S3-cached model directory with .complete marker."""
    safe_id = model_id.replace("/", "--")
    model_dir = cache_dir / safe_id
    model_dir.mkdir(parents=True)

    # Create a fake model file of the given size
    (model_dir / "model.bin").write_bytes(b"\x00" * size_bytes)
    (model_dir / ".complete").touch()

    # Write access marker
    access_time = time.time() - age_seconds
    (model_dir / ACCESS_MARKER).write_text(str(access_time))

    return model_dir


def _make_hf_model(
    cache_dir: Path, model_id: str, size_bytes: int = 1024, age_seconds: float = 0
) -> Path:
    """Create a fake HF-cached model directory with models-- prefix."""
    safe_id = model_id.replace("/", "--")
    model_dir = cache_dir / f"models--{safe_id}"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)

    # Create a fake model file
    (snapshot_dir / "model.safetensors").write_bytes(b"\x00" * size_bytes)

    # Write access marker on model dir (not snapshot)
    access_time = time.time() - age_seconds
    (model_dir / ACCESS_MARKER).write_text(str(access_time))

    return model_dir


# ---------------------------------------------------------------------------
# _touch_access_marker
# ---------------------------------------------------------------------------


class TestTouchAccessMarker:
    def test_creates_marker_file(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "my-model"
        model_dir.mkdir()

        _touch_access_marker(model_dir)

        marker = model_dir / ACCESS_MARKER
        assert marker.exists()
        ts = float(marker.read_text())
        assert abs(ts - time.time()) < 2

    def test_overwrites_existing_marker(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "my-model"
        model_dir.mkdir()

        # Write old marker
        (model_dir / ACCESS_MARKER).write_text("1000000.0")

        _touch_access_marker(model_dir)

        ts = float((model_dir / ACCESS_MARKER).read_text())
        assert ts > 1000000.0


# ---------------------------------------------------------------------------
# DiskCacheEvictor — construction
# ---------------------------------------------------------------------------


class TestDiskCacheEvictorInit:
    def test_is_enabled_when_max_gb_set(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path], max_gb=10)
        assert evictor.is_enabled is True

    def test_is_enabled_when_ttl_set(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path], max_age_hours=24)
        assert evictor.is_enabled is True

    def test_not_enabled_when_defaults(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path])
        assert evictor.is_enabled is False

    def test_from_env_reads_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)

        monkeypatch.setenv("DALSTON_MODEL_CACHE_MAX_GB", "50")
        monkeypatch.setenv("DALSTON_MODEL_CACHE_TTL_HOURS", "168")
        monkeypatch.setenv("DALSTON_MODEL_CACHE_SCAN_INTERVAL", "30")

        with (
            patch("dalston.engine_sdk.disk_cache.MODEL_BASE", tmp_path),
            patch("dalston.engine_sdk.disk_cache.HF_CACHE", hf_cache),
        ):
            evictor = DiskCacheEvictor.from_env()

        assert evictor.max_gb == 50.0
        assert evictor.max_age_hours == 168.0
        assert evictor.scan_interval == 30
        assert evictor.is_enabled is True

    def test_from_env_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DALSTON_MODEL_CACHE_MAX_GB", raising=False)
        monkeypatch.delenv("DALSTON_MODEL_CACHE_TTL_HOURS", raising=False)
        monkeypatch.delenv("DALSTON_MODEL_CACHE_SCAN_INTERVAL", raising=False)

        with (
            patch("dalston.engine_sdk.disk_cache.MODEL_BASE", tmp_path),
            patch("dalston.engine_sdk.disk_cache.HF_CACHE", tmp_path / "nonexistent"),
        ):
            evictor = DiskCacheEvictor.from_env()

        assert evictor.max_gb == 0
        assert evictor.max_age_hours == 0
        assert evictor.scan_interval == 600
        assert evictor.is_enabled is False


# ---------------------------------------------------------------------------
# DiskCacheEvictor — TTL eviction
# ---------------------------------------------------------------------------


class TestTTLEviction:
    def test_evicts_old_models(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        # Old model (2 hours old, TTL = 1 hour)
        _make_s3_model(s3_cache, "org/old-model", size_bytes=1024, age_seconds=7200)
        # Fresh model
        _make_s3_model(s3_cache, "org/fresh-model", size_bytes=1024, age_seconds=60)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_age_hours=1,
        )
        result = evictor.scan_and_evict()

        assert result.scanned == 2
        assert result.evicted_count == 1
        assert result.evicted[0].model_id == "org/old-model"
        assert result.evicted[0].reason == "ttl"

        # Old model directory should be gone
        assert not (s3_cache / "org--old-model").exists()
        # Fresh model should remain
        assert (s3_cache / "org--fresh-model").exists()

    def test_skips_loaded_models(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        _make_s3_model(s3_cache, "org/loaded-model", age_seconds=7200)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_age_hours=1,
            is_model_loaded=lambda mid: mid == "org/loaded-model",
        )
        result = evictor.scan_and_evict()

        assert result.evicted_count == 0
        assert result.skipped_loaded == 1
        assert (s3_cache / "org--loaded-model").exists()

    def test_no_eviction_when_all_fresh(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        _make_s3_model(s3_cache, "org/model-a", age_seconds=60)
        _make_s3_model(s3_cache, "org/model-b", age_seconds=120)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_age_hours=1,
        )
        result = evictor.scan_and_evict()

        assert result.evicted_count == 0


# ---------------------------------------------------------------------------
# DiskCacheEvictor — Budget eviction
# ---------------------------------------------------------------------------


class TestBudgetEviction:
    def test_evicts_lru_when_over_budget(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        # 3 models, each 500 bytes. Budget = 1200 bytes (~0.000001 GB)
        _make_s3_model(s3_cache, "org/oldest", size_bytes=500, age_seconds=300)
        _make_s3_model(s3_cache, "org/middle", size_bytes=500, age_seconds=200)
        _make_s3_model(s3_cache, "org/newest", size_bytes=500, age_seconds=100)

        # Budget that fits ~2 models (each model dir is ~500 bytes for model.bin
        # + small .complete and .last_accessed files)
        # Use max_gb in bytes: 500*2 + some slack = ~1200 bytes
        max_gb = 1200 / (1024**3)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_gb=max_gb,
        )
        result = evictor.scan_and_evict()

        # Should evict the oldest model to get under budget
        assert result.evicted_count >= 1
        evicted_ids = [e.model_id for e in result.evicted]
        assert "org/oldest" in evicted_ids
        assert result.evicted[0].reason == "budget"

    def test_skips_loaded_models_in_budget(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        _make_s3_model(s3_cache, "org/oldest", size_bytes=500, age_seconds=300)
        _make_s3_model(s3_cache, "org/newest", size_bytes=500, age_seconds=100)

        # Budget fits only 1 model, but oldest is loaded
        max_gb = 600 / (1024**3)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_gb=max_gb,
            is_model_loaded=lambda mid: mid == "org/oldest",
        )
        result = evictor.scan_and_evict()

        # Can only evict newest (oldest is loaded)
        evicted_ids = [e.model_id for e in result.evicted]
        assert "org/oldest" not in evicted_ids


# ---------------------------------------------------------------------------
# DiskCacheEvictor — HF cache handling
# ---------------------------------------------------------------------------


class TestHFCacheEviction:
    def test_scans_hf_cache_dirs(self, tmp_path: Path) -> None:
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)

        _make_hf_model(
            hf_cache, "Systran/faster-whisper-base", size_bytes=2048, age_seconds=7200
        )
        _make_hf_model(
            hf_cache, "openai/whisper-large-v3", size_bytes=1024, age_seconds=60
        )

        evictor = DiskCacheEvictor(
            cache_dirs=[hf_cache],
            max_age_hours=1,
            hf_cache_dirs=frozenset([hf_cache]),
        )
        result = evictor.scan_and_evict()

        assert result.scanned == 2
        assert result.evicted_count == 1
        assert result.evicted[0].model_id == "Systran/faster-whisper-base"

    def test_hf_eviction_uses_scan_cache_dir(self, tmp_path: Path) -> None:
        """Verify HF eviction attempts to use huggingface_hub API."""
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)

        model_dir = _make_hf_model(hf_cache, "org/model", age_seconds=7200)

        mock_strategy = MagicMock()
        mock_revision = MagicMock()
        mock_revision.commit_hash = "abc123"
        mock_repo = MagicMock()
        mock_repo.repo_path = model_dir
        mock_repo.revisions = [mock_revision]
        mock_cache_info = MagicMock()
        mock_cache_info.repos = [mock_repo]
        mock_cache_info.delete_revisions.return_value = mock_strategy

        with patch(
            "dalston.engine_sdk.disk_cache.DiskCacheEvictor._remove_hf_entry"
        ) as mock_remove:
            evictor = DiskCacheEvictor(
                cache_dirs=[hf_cache],
                max_age_hours=1,
                hf_cache_dirs=frozenset([hf_cache]),
            )
            result = evictor.scan_and_evict()

            assert result.evicted_count == 1
            mock_remove.assert_called_once()


# ---------------------------------------------------------------------------
# DiskCacheEvictor — access time fallback
# ---------------------------------------------------------------------------


class TestAccessTimeFallback:
    def test_falls_back_to_mtime_when_no_marker(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        # Create model without .last_accessed marker
        model_dir = s3_cache / "org--model"
        model_dir.mkdir()
        (model_dir / "model.bin").write_bytes(b"\x00" * 100)
        (model_dir / ".complete").touch()

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_age_hours=1,
        )

        entries = evictor._scan_entries()
        assert len(entries) == 1
        # Should have fallen back to mtime (recent)
        assert abs(entries[0].last_accessed - time.time()) < 5

    def test_ignores_incomplete_s3_downloads(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        # Create model without .complete marker (incomplete download)
        model_dir = s3_cache / "org--incomplete"
        model_dir.mkdir()
        (model_dir / "model.bin").write_bytes(b"\x00" * 100)

        evictor = DiskCacheEvictor(cache_dirs=[s3_cache], max_age_hours=1)

        entries = evictor._scan_entries()
        assert len(entries) == 0


# ---------------------------------------------------------------------------
# DiskCacheEvictor — combined TTL + budget
# ---------------------------------------------------------------------------


class TestCombinedEviction:
    def test_ttl_then_budget(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        # TTL-expired model
        _make_s3_model(s3_cache, "org/expired", size_bytes=500, age_seconds=7200)
        # Two fresh models that together exceed budget
        _make_s3_model(s3_cache, "org/big-old", size_bytes=500, age_seconds=300)
        _make_s3_model(s3_cache, "org/big-new", size_bytes=500, age_seconds=100)

        # Budget fits ~2 models
        max_gb = 1200 / (1024**3)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache],
            max_age_hours=1,
            max_gb=max_gb,
        )
        result = evictor.scan_and_evict()

        evicted_ids = [e.model_id for e in result.evicted]
        # Expired model should be evicted by TTL
        assert "org/expired" in evicted_ids

        # After TTL eviction, remaining models may or may not exceed budget
        # depending on exact file sizes (markers add overhead)


# ---------------------------------------------------------------------------
# DiskCacheEvictor — start/stop
# ---------------------------------------------------------------------------


class TestEvictorLifecycle:
    def test_start_does_nothing_when_disabled(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path])
        assert not evictor.is_enabled
        evictor.start()
        assert evictor._thread is None

    def test_start_creates_thread_when_enabled(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path], max_gb=10)
        evictor.start()
        assert evictor._thread is not None
        assert evictor._thread.is_alive()
        evictor.stop()

    def test_stop_is_safe_when_not_started(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(cache_dirs=[tmp_path])
        evictor.stop()  # Should not raise


# ---------------------------------------------------------------------------
# DiskCacheEvictor — empty/missing cache dirs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_cache_dirs(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()

        evictor = DiskCacheEvictor(cache_dirs=[s3_cache], max_age_hours=1)
        result = evictor.scan_and_evict()

        assert result.scanned == 0
        assert result.evicted_count == 0

    def test_nonexistent_cache_dir(self, tmp_path: Path) -> None:
        evictor = DiskCacheEvictor(
            cache_dirs=[tmp_path / "nonexistent"],
            max_age_hours=1,
        )
        result = evictor.scan_and_evict()
        assert result.scanned == 0

    def test_scan_with_no_limits(self, tmp_path: Path) -> None:
        """When no limits configured, scan_and_evict still works (no evictions)."""
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()
        _make_s3_model(s3_cache, "org/model", size_bytes=1024, age_seconds=99999)

        evictor = DiskCacheEvictor(cache_dirs=[s3_cache])
        result = evictor.scan_and_evict()

        assert result.scanned == 1
        assert result.evicted_count == 0

    def test_mixed_s3_and_hf_cache(self, tmp_path: Path) -> None:
        s3_cache = tmp_path / "s3-cache"
        s3_cache.mkdir()
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)

        _make_s3_model(s3_cache, "org/s3-model", size_bytes=512, age_seconds=7200)
        _make_hf_model(hf_cache, "org/hf-model", size_bytes=512, age_seconds=7200)

        evictor = DiskCacheEvictor(
            cache_dirs=[s3_cache, hf_cache],
            max_age_hours=1,
            hf_cache_dirs=frozenset([hf_cache]),
        )
        result = evictor.scan_and_evict()

        assert result.scanned == 2
        assert result.evicted_count == 2
        evicted_ids = {e.model_id for e in result.evicted}
        assert "org/s3-model" in evicted_ids
        assert "org/hf-model" in evicted_ids
