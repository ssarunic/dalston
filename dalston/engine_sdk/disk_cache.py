"""Background disk cache evictor for on-disk model files.

Scans model cache directories and removes model directories that are:
- Older than max_age_hours since last access (TTL eviction)
- Over the max_gb disk budget (LRU eviction — oldest first)

Models currently loaded in memory (via ModelManager) are never evicted.

Environment variables:
    DALSTON_MODEL_CACHE_MAX_GB: Max disk usage in GB (default: 0 = unlimited)
    DALSTON_MODEL_CACHE_TTL_HOURS: Max hours since last access (default: 0 = unlimited)
    DALSTON_MODEL_CACHE_SCAN_INTERVAL: Seconds between scans (default: 600)
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from dalston.engine_sdk.model_paths import HF_CACHE, MODEL_BASE
from dalston.engine_sdk.model_storage import ACCESS_MARKER

if TYPE_CHECKING:
    from collections.abc import Callable

    from dalston.engine_sdk.model_manager import ModelManager
    from dalston.engine_sdk.model_storage import MultiSourceModelStorage

logger = structlog.get_logger()


@dataclass
class EvictedModel:
    """Record of an evicted model."""

    model_id: str
    path: Path
    size_bytes: int
    reason: str  # "ttl" or "budget"


@dataclass
class EvictionResult:
    """Result of a single eviction scan pass."""

    scanned: int = 0
    evicted: list[EvictedModel] = field(default_factory=list)
    skipped_loaded: int = 0
    total_size_after: int = 0

    @property
    def evicted_count(self) -> int:
        return len(self.evicted)

    @property
    def evicted_bytes(self) -> int:
        return sum(e.size_bytes for e in self.evicted)


@dataclass
class _CacheEntry:
    """Internal representation of a cached model directory."""

    model_id: str
    path: Path
    size_bytes: int
    last_accessed: float
    is_hf: bool  # True for HF cache entries (need special deletion)


class DiskCacheEvictor:
    """Background evictor for on-disk model cache.

    Runs a periodic scan of the model cache directory and removes
    model directories that are:
    - Older than max_age_hours since last access (TTL eviction)
    - Over the max_gb disk budget (LRU eviction — oldest first)

    Models currently loaded in memory (via ModelManager) are never
    evicted from disk.

    Environment variables:
        DALSTON_MODEL_CACHE_MAX_GB: Max disk usage in GB (default: 0 = unlimited)
        DALSTON_MODEL_CACHE_TTL_HOURS: Max hours since last access (default: 0 = unlimited)
        DALSTON_MODEL_CACHE_SCAN_INTERVAL: Seconds between scans (default: 600)
    """

    def __init__(
        self,
        cache_dirs: list[Path],
        max_gb: float = 0,
        max_age_hours: float = 0,
        scan_interval: int = 600,
        is_model_loaded: Callable[[str], bool] | None = None,
        hf_cache_dirs: frozenset[Path] | None = None,
    ) -> None:
        self.cache_dirs = cache_dirs
        self._hf_cache_dirs = hf_cache_dirs or frozenset()
        self.max_gb = max_gb
        self.max_age_hours = max_age_hours
        self.scan_interval = scan_interval
        self._is_model_loaded = is_model_loaded

        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    @classmethod
    def from_env(
        cls,
        is_model_loaded: Callable[[str], bool] | None = None,
    ) -> DiskCacheEvictor:
        """Create evictor configured from environment variables."""
        s3_cache = MODEL_BASE / "s3-cache"
        cache_dirs = [d for d in [s3_cache, HF_CACHE] if d.exists()]
        hf_cache_dirs = frozenset([HF_CACHE]) if HF_CACHE.exists() else frozenset()

        return cls(
            cache_dirs=cache_dirs,
            max_gb=float(os.environ.get("DALSTON_MODEL_CACHE_MAX_GB", "0")),
            max_age_hours=float(os.environ.get("DALSTON_MODEL_CACHE_TTL_HOURS", "0")),
            scan_interval=int(
                os.environ.get("DALSTON_MODEL_CACHE_SCAN_INTERVAL", "600")
            ),
            is_model_loaded=is_model_loaded,
            hf_cache_dirs=hf_cache_dirs,
        )

    @property
    def is_enabled(self) -> bool:
        """True if at least one eviction limit is configured."""
        return self.max_gb > 0 or self.max_age_hours > 0

    def start(self) -> None:
        """Start the background eviction thread."""
        if not self.is_enabled:
            logger.info("disk_cache_evictor_disabled", reason="no limits configured")
            return

        logger.info(
            "disk_cache_evictor_starting",
            max_gb=self.max_gb,
            max_age_hours=self.max_age_hours,
            scan_interval=self.scan_interval,
            cache_dirs=[str(d) for d in self.cache_dirs],
        )

        def _loop() -> None:
            while not self._shutdown.wait(timeout=self.scan_interval):
                try:
                    self.scan_and_evict()
                except Exception:
                    logger.exception("disk_cache_eviction_error")

        self._thread = threading.Thread(
            target=_loop,
            daemon=True,
            name="disk-cache-evictor",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background eviction thread."""
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def scan_and_evict(self) -> EvictionResult:
        """Run one eviction pass.

        Called by background thread and available for manual/test use.
        """
        result = EvictionResult()

        # Collect all cache entries
        entries = self._scan_entries()
        result.scanned = len(entries)

        # Filter out loaded models
        eligible: list[_CacheEntry] = []
        for entry in entries:
            if self._is_model_loaded and self._is_model_loaded(entry.model_id):
                result.skipped_loaded += 1
            else:
                eligible.append(entry)

        now = time.time()

        # TTL pass: remove entries older than max_age_hours
        if self.max_age_hours > 0:
            max_age_seconds = self.max_age_hours * 3600
            surviving: list[_CacheEntry] = []
            for entry in eligible:
                age = now - entry.last_accessed
                if age > max_age_seconds:
                    self._remove_entry(entry)
                    result.evicted.append(
                        EvictedModel(
                            model_id=entry.model_id,
                            path=entry.path,
                            size_bytes=entry.size_bytes,
                            reason="ttl",
                        )
                    )
                    logger.info(
                        "disk_cache_evicted",
                        model_id=entry.model_id,
                        reason="ttl",
                        age_hours=round(age / 3600, 1),
                    )
                else:
                    surviving.append(entry)
            eligible = surviving

        # Budget pass: remove LRU entries if total exceeds max_gb
        if self.max_gb > 0:
            max_bytes = int(self.max_gb * 1024 * 1024 * 1024)
            # Include loaded models in total (they count toward budget but can't be evicted)
            evicted_paths = {ev.path for ev in result.evicted}
            total = sum(e.size_bytes for e in entries if e.path not in evicted_paths)

            # Sort eligible by last_accessed ascending (oldest first)
            eligible.sort(key=lambda e: e.last_accessed)

            for entry in eligible:
                if total <= max_bytes:
                    break
                self._remove_entry(entry)
                total -= entry.size_bytes
                result.evicted.append(
                    EvictedModel(
                        model_id=entry.model_id,
                        path=entry.path,
                        size_bytes=entry.size_bytes,
                        reason="budget",
                    )
                )
                logger.info(
                    "disk_cache_evicted",
                    model_id=entry.model_id,
                    reason="budget",
                    total_gb=round(total / (1024**3), 2),
                    max_gb=self.max_gb,
                )

            result.total_size_after = total
        else:
            evicted_paths = {ev.path for ev in result.evicted}
            result.total_size_after = sum(
                e.size_bytes for e in entries if e.path not in evicted_paths
            )

        if result.evicted:
            logger.info(
                "disk_cache_eviction_pass_complete",
                scanned=result.scanned,
                evicted=result.evicted_count,
                evicted_mb=round(result.evicted_bytes / (1024 * 1024), 1),
                remaining_mb=round(result.total_size_after / (1024 * 1024), 1),
            )

        return result

    def _scan_entries(self) -> list[_CacheEntry]:
        """Scan all cache directories and return cache entries."""
        entries: list[_CacheEntry] = []

        for cache_dir in self.cache_dirs:
            if not cache_dir.exists():
                continue

            is_hf = cache_dir in self._hf_cache_dirs

            for item in cache_dir.iterdir():
                if not item.is_dir():
                    continue

                # For HF cache, model dirs are named "models--org--name"
                if is_hf and not item.name.startswith("models--"):
                    continue

                # For S3 cache, skip incomplete downloads (no .complete marker)
                if not is_hf and not (item / ".complete").exists():
                    continue

                model_id = self._dir_to_model_id(item, is_hf)
                size = self._dir_size(item)
                last_accessed = self._read_access_time(item)

                entries.append(
                    _CacheEntry(
                        model_id=model_id,
                        path=item,
                        size_bytes=size,
                        last_accessed=last_accessed,
                        is_hf=is_hf,
                    )
                )

        return entries

    def _dir_to_model_id(self, path: Path, is_hf: bool) -> str:
        """Convert a cache directory name back to a model_id."""
        name = path.name
        if is_hf:
            # models--Systran--faster-whisper-base → Systran/faster-whisper-base
            return name.removeprefix("models--").replace("--", "/")
        else:
            # Systran--faster-whisper-base → Systran/faster-whisper-base
            return name.replace("--", "/")

    def _dir_size(self, path: Path) -> int:
        """Calculate total size of a directory in bytes."""
        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def _read_access_time(self, path: Path) -> float:
        """Read last access time from .last_accessed marker, or fall back to mtime."""
        try:
            return float((path / ACCESS_MARKER).read_text().strip())
        except (ValueError, OSError):
            pass
        # Fall back to directory mtime
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _remove_entry(self, entry: _CacheEntry) -> None:
        """Remove a cache entry from disk."""
        if entry.is_hf:
            self._remove_hf_entry(entry)
        else:
            shutil.rmtree(entry.path, ignore_errors=True)

    def _remove_hf_entry(self, entry: _CacheEntry) -> None:
        """Remove an HF cache entry using huggingface_hub's cache management."""
        try:
            from huggingface_hub import scan_cache_dir

            # Scan the HF cache to find revisions for this model
            hub_dir = entry.path.parent  # The hub/ directory
            cache_info = scan_cache_dir(hub_dir)

            for repo_info in cache_info.repos:
                if repo_info.repo_path == entry.path:
                    # Collect all revision commit hashes
                    revisions = [rev.commit_hash for rev in repo_info.revisions]
                    if revisions:
                        strategy = cache_info.delete_revisions(*revisions)
                        strategy.execute()
                        logger.debug(
                            "hf_cache_revisions_deleted",
                            model_id=entry.model_id,
                            revisions=len(revisions),
                        )
                    return

            # Fallback if repo not found in scan (shouldn't happen)
            logger.warning(
                "hf_cache_repo_not_found_in_scan",
                model_id=entry.model_id,
                path=str(entry.path),
            )
            shutil.rmtree(entry.path, ignore_errors=True)

        except ImportError:
            # huggingface_hub not installed — fall back to rmtree
            shutil.rmtree(entry.path, ignore_errors=True)
        except Exception:
            logger.exception(
                "hf_cache_deletion_error",
                model_id=entry.model_id,
            )
            # Last resort fallback
            shutil.rmtree(entry.path, ignore_errors=True)


def start_disk_evictor(
    manager: ModelManager,
    model_storage: MultiSourceModelStorage | None,
) -> DiskCacheEvictor | None:
    """Create and start a disk cache evictor for a model manager.

    Returns the evictor if enabled, None otherwise.
    """
    evictor = DiskCacheEvictor.from_env(
        is_model_loaded=lambda model_id: manager.is_loaded(model_id),
    )
    if not evictor.is_enabled:
        return None

    evictor.start()
    if model_storage is not None:
        model_storage.set_disk_evictor(evictor)
    return evictor
