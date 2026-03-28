"""VRAM budget calculator for GPU engine parameter auto-tuning.

Loads a calibration profile (produced by ``dalston.tools.calibrate_vram``)
and computes optimal engine parameters for a given VRAM budget.  Returns
two parameter sets — *solo* (high batch, single file) and *concurrent*
(low batch, many files) — that the engine switches between at task time
based on queue depth.

When no calibration profile exists, conservative defaults are used so
engines always start safely.

Environment variables
---------------------
DALSTON_VRAM_BUDGET_MB : int
    Explicit VRAM budget in megabytes.
DALSTON_VRAM_SHARE : float
    Fraction of total detected VRAM (e.g. ``0.45`` for 45%).
DALSTON_VRAM_PROFILE_DIR : str
    Additional directory to search for calibration profiles.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Where to find shipped profiles (inside the package)
# ---------------------------------------------------------------------------

_BUILTIN_PROFILE_DIR = (
    Path(__file__).resolve().parent.parent / "tools" / "vram_profiles"
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EngineVRAMParams:
    """A single parameter set for engine operation."""

    # Transcription parameters
    vad_batch_size: int = 1
    vad_max_speech_s: float = 60.0
    batch_max_inflight: int = 1
    max_sessions: int = 2

    # Diarization parameters
    max_diarize_chunk_s: float = 900.0
    diarize_overlap_s: float = 30.0

    # Metadata
    peak_estimate_mb: int = 0
    headroom_mb: int = 0


@dataclass
class AdaptiveVRAMParams:
    """Two parameter sets for adaptive per-task selection."""

    solo: EngineVRAMParams  # queue_depth <= 1: high batch, N=1
    concurrent: EngineVRAMParams  # queue_depth > 1:  low batch, high N

    budget_mb: int = 0
    profile_source: str = "defaults"  # "calibrated" | "fallback" | "defaults"

    def select(self, queue_depth: int, inflight: int) -> EngineVRAMParams:
        """Pick the right param set based on current load.

        Uses solo params when the GPU would otherwise be idle between
        small inference calls.  Switches to concurrent params when
        there is enough work to keep the GPU busy across files.
        """
        if queue_depth <= 1 and inflight == 0:
            return self.solo
        return self.concurrent


# ---------------------------------------------------------------------------
# Calibration profile schema
# ---------------------------------------------------------------------------


@dataclass
class CalibrationProfile:
    """Parsed calibration profile from JSON."""

    engine_id: str = ""
    model_id: str = ""
    stage: str = ""
    gpu: str = ""
    gpu_vram_mb: int = 0
    cuda_overhead_mb: int = 650
    weights_mb: int = 0
    framework_overhead_mb: int = 200
    safety_margin: float = 0.15
    coefficients: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationProfile:
        model = data.get("model", {})
        return cls(
            engine_id=data.get("engine_id", ""),
            model_id=data.get("model_id", ""),
            stage=data.get("stage", ""),
            gpu=data.get("gpu", ""),
            gpu_vram_mb=data.get("gpu_vram_mb", 0),
            cuda_overhead_mb=data.get("cuda_overhead_mb", 650),
            weights_mb=model.get("weights_mb", 0),
            framework_overhead_mb=model.get("framework_overhead_mb", 200),
            safety_margin=model.get("safety_margin", 0.15),
            coefficients=model.get("coefficients", {}),
        )


# ---------------------------------------------------------------------------
# VRAMBudget
# ---------------------------------------------------------------------------


class VRAMBudget:
    """Compute engine parameters from a VRAM budget and calibration profile."""

    def __init__(self, profile: CalibrationProfile) -> None:
        self._profile = profile

    @property
    def profile(self) -> CalibrationProfile:
        return self._profile

    # -- Factory methods ----------------------------------------------------

    @classmethod
    def load(
        cls,
        engine_id: str,
        model_id: str,
        gpu_name: str | None = None,
    ) -> VRAMBudget:
        """Load calibration profile for the given engine/model/GPU.

        Search order:
        1. ``DALSTON_VRAM_PROFILE_DIR`` (if set)
        2. Built-in profiles shipped with the package

        Falls back to an empty profile (conservative defaults) if no
        match is found.
        """
        search_dirs: list[Path] = []

        custom_dir = os.environ.get("DALSTON_VRAM_PROFILE_DIR")
        if custom_dir:
            search_dirs.append(Path(custom_dir))
        search_dirs.append(_BUILTIN_PROFILE_DIR)

        for d in search_dirs:
            if not d.is_dir():
                continue
            for f in d.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                if (
                    data.get("engine_id") == engine_id
                    and data.get("model_id") == model_id
                ):
                    if gpu_name and data.get("gpu") and data["gpu"] != gpu_name:
                        logger.warning(
                            "vram_profile_gpu_mismatch",
                            profile_gpu=data["gpu"],
                            actual_gpu=gpu_name,
                            file=str(f),
                        )
                    logger.info(
                        "vram_profile_loaded",
                        file=str(f),
                        engine_id=engine_id,
                        model_id=model_id,
                    )
                    return cls(CalibrationProfile.from_dict(data))

        logger.info(
            "vram_profile_not_found",
            engine_id=engine_id,
            model_id=model_id,
            search_dirs=[str(d) for d in search_dirs],
        )
        return cls(CalibrationProfile())

    @classmethod
    def from_profile_file(cls, path: str | Path) -> VRAMBudget:
        """Load a specific profile file directly."""
        data = json.loads(Path(path).read_text())
        return cls(CalibrationProfile.from_dict(data))

    # -- Computation --------------------------------------------------------

    def compute_adaptive_params(self, budget_mb: int) -> AdaptiveVRAMParams:
        """Compute both solo and concurrent parameter sets.

        Both sets are guaranteed to fit within *budget_mb* including
        the safety margin.
        """
        p = self._profile
        has_profile = bool(p.coefficients)

        if has_profile:
            return self._compute_from_profile(budget_mb)
        return self._compute_defaults(budget_mb)

    def _compute_from_profile(self, budget_mb: int) -> AdaptiveVRAMParams:
        """Compute params using calibration coefficients."""
        p = self._profile
        headroom = int(budget_mb * p.safety_margin)
        available = (
            budget_mb
            - p.cuda_overhead_mb
            - p.weights_mb
            - p.framework_overhead_mb
            - headroom
        )

        if available <= 0:
            logger.warning(
                "vram_budget_insufficient",
                budget_mb=budget_mb,
                overhead=p.cuda_overhead_mb + p.weights_mb + p.framework_overhead_mb,
                headroom=headroom,
            )
            return self._compute_defaults(budget_mb)

        alpha_batch = p.coefficients.get("alpha_batch", 55.0)
        alpha_beam = p.coefficients.get("alpha_beam", 0.0)
        S = p.coefficients.get("S", 0.0)

        # For faster-whisper profiles with alpha_beam, hold beam_size at
        # the default (5) and optimise batch_size within the remaining budget.
        default_beam_size = 5
        beam_cost = alpha_beam * default_beam_size

        # Solo: maximize vad_batch_size, inflight=1
        # Peak = S + alpha_batch * batch_size + beam_cost  (must fit in available)
        solo_headroom_for_batch = available - S - beam_cost
        if solo_headroom_for_batch > 0 and alpha_batch > 0:
            solo_batch = max(1, int(solo_headroom_for_batch / alpha_batch))
        else:
            solo_batch = 1

        solo_peak = int(
            S
            + alpha_batch * solo_batch
            + beam_cost
            + p.cuda_overhead_mb
            + p.weights_mb
            + p.framework_overhead_mb
        )

        # Concurrent: vad_batch_size=1, maximize inflight
        # Each inflight request uses: S + alpha_batch * 1 + beam_cost
        activation_per_request = S + alpha_batch + beam_cost
        if activation_per_request > 0:
            concurrent_inflight = max(1, int(available / activation_per_request))
        else:
            concurrent_inflight = 1

        concurrent_peak = int(
            activation_per_request * concurrent_inflight
            + p.cuda_overhead_mb
            + p.weights_mb
            + p.framework_overhead_mb
        )

        # Diarize chunk limit from duration coefficient (if present)
        alpha_duration = p.coefficients.get("alpha_duration", 0.0)
        if alpha_duration > 0:
            max_diarize_chunk_s = max(60.0, available / alpha_duration)
        else:
            max_diarize_chunk_s = 900.0

        solo = EngineVRAMParams(
            vad_batch_size=solo_batch,
            batch_max_inflight=1,
            max_sessions=2,
            max_diarize_chunk_s=max_diarize_chunk_s,
            peak_estimate_mb=solo_peak,
            headroom_mb=budget_mb - solo_peak,
        )
        concurrent = EngineVRAMParams(
            vad_batch_size=1,
            batch_max_inflight=concurrent_inflight,
            max_sessions=max(2, concurrent_inflight),
            max_diarize_chunk_s=max_diarize_chunk_s,
            peak_estimate_mb=concurrent_peak,
            headroom_mb=budget_mb - concurrent_peak,
        )

        return AdaptiveVRAMParams(
            solo=solo,
            concurrent=concurrent,
            budget_mb=budget_mb,
            profile_source="calibrated",
        )

    def _compute_defaults(self, budget_mb: int) -> AdaptiveVRAMParams:
        """Conservative defaults when no calibration profile exists."""
        solo = EngineVRAMParams(
            vad_batch_size=4,
            batch_max_inflight=1,
            max_sessions=2,
            max_diarize_chunk_s=600.0,
        )
        concurrent = EngineVRAMParams(
            vad_batch_size=1,
            batch_max_inflight=2,
            max_sessions=2,
            max_diarize_chunk_s=600.0,
        )
        return AdaptiveVRAMParams(
            solo=solo,
            concurrent=concurrent,
            budget_mb=budget_mb,
            profile_source="defaults",
        )


# ---------------------------------------------------------------------------
# VRAM budget resolution from environment
# ---------------------------------------------------------------------------


def resolve_vram_budget() -> int | None:
    """Resolve VRAM budget from environment variables.

    Returns budget in MB, or None if no budget is configured.
    Checks ``DALSTON_VRAM_BUDGET_MB`` first, then ``DALSTON_VRAM_SHARE``.
    """
    if explicit := os.environ.get("DALSTON_VRAM_BUDGET_MB"):
        return int(explicit)

    share = os.environ.get("DALSTON_VRAM_SHARE")
    if share:
        total = _get_gpu_total_mb()
        if total > 0:
            return int(total * float(share))
        logger.warning(
            "vram_share_no_gpu",
            share=share,
            reason="no GPU detected, cannot compute VRAM budget from share",
        )

    return None


def _get_gpu_total_mb() -> int:
    """Get total VRAM of GPU 0 in MB using pynvml, falling back to torch."""
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        total = pynvml.nvmlDeviceGetMemoryInfo(handle).total // (1024 * 1024)
        pynvml.nvmlShutdown()
        return int(total)
    except Exception:
        pass

    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.get_device_properties(0).total_mem / (1024 * 1024))
    except Exception:
        pass

    return 0
