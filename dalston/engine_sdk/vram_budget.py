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


def _find_profile_data(
    engine_id: str,
    model_id: str,
    gpu_name: str | None = None,
) -> dict[str, Any] | None:
    """Search for a calibration profile JSON matching engine/model/GPU.

    Search order:
    1. ``DALSTON_VRAM_PROFILE_DIR`` (if set)
    2. Built-in profiles shipped with the package

    When *gpu_name* is provided, an exact GPU match is preferred.  A
    profile for a different GPU is kept as a fallback and used only when
    no exact match exists (with a warning).  This prevents, e.g., an A10
    concurrency profile from being blindly applied on an L4.

    Returns the raw dict if found, or None.
    """
    search_dirs: list[Path] = []

    custom_dir = os.environ.get("DALSTON_VRAM_PROFILE_DIR")
    if custom_dir:
        search_dirs.append(Path(custom_dir))
    search_dirs.append(_BUILTIN_PROFILE_DIR)

    fallback_match: dict[str, Any] | None = None
    fallback_file: str = ""

    for d in search_dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("engine_id") != engine_id or data.get("model_id") != model_id:
                continue

            profile_gpu = data.get("gpu")
            if not gpu_name or not profile_gpu or profile_gpu == gpu_name:
                logger.info(
                    "vram_profile_loaded",
                    file=str(f),
                    engine_id=engine_id,
                    model_id=model_id,
                )
                return data

            # GPU mismatch — keep as fallback
            if fallback_match is None:
                fallback_match = data
                fallback_file = str(f)

    if fallback_match is not None:
        logger.warning(
            "vram_profile_gpu_mismatch",
            profile_gpu=fallback_match.get("gpu"),
            actual_gpu=gpu_name,
            file=fallback_file,
            message="No exact GPU match found, using mismatched profile",
        )
        return fallback_match

    logger.info(
        "vram_profile_not_found",
        engine_id=engine_id,
        model_id=model_id,
        search_dirs=[str(d) for d in search_dirs],
    )
    return None


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

        Falls back to an empty profile (conservative defaults) if no
        match is found.
        """
        data = _find_profile_data(engine_id, model_id, gpu_name)
        if data is not None:
            return cls(CalibrationProfile.from_dict(data))
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
# vLLM throughput profile
# ---------------------------------------------------------------------------


@dataclass
class VllmAdmissionParams:
    """Computed admission control parameters from a vLLM throughput profile.

    Unlike VRAM-based engines that tune batch_size, vLLM pre-allocates GPU
    memory and the tunable is concurrency.  This dataclass provides the
    admission controller with safe limits derived from calibration.
    """

    total_capacity: int = 6
    batch_max_inflight: int = 4
    rt_reservation: int = 2
    throughput_rps: float = 0.0
    latency_per_audio_s: float = 0.15
    profile_source: str = "defaults"


@dataclass
class VllmCalibrationProfile:
    """Parsed vLLM calibration profile."""

    engine_id: str = ""
    model_id: str = ""
    gpu: str = ""
    gpu_vram_mb: int = 0
    max_safe_concurrency: int = 6
    optimal_concurrency: int = 4
    throughput_at_max: float = 0.0
    latency_per_audio_s: float = 0.15

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VllmCalibrationProfile:
        model = data.get("model", {})
        coefficients = model.get("coefficients", {})
        return cls(
            engine_id=data.get("engine_id", ""),
            model_id=data.get("model_id", ""),
            gpu=data.get("gpu", ""),
            gpu_vram_mb=data.get("gpu_vram_mb", 0),
            max_safe_concurrency=int(coefficients.get("max_safe_concurrency", 6)),
            optimal_concurrency=int(coefficients.get("optimal_concurrency", 4)),
            throughput_at_max=coefficients.get("throughput_at_max", 0.0),
            latency_per_audio_s=coefficients.get("latency_per_audio_s", 0.15),
        )


def load_vllm_profile(
    model_id: str,
    gpu_name: str | None = None,
) -> VllmCalibrationProfile | None:
    """Load a vLLM calibration profile for the given model/GPU.

    Uses the same search path as VRAMBudget.load().  Returns None if
    no matching vllm-asr profile is found.
    """
    data = _find_profile_data("vllm-asr", model_id, gpu_name)
    if data is not None:
        return VllmCalibrationProfile.from_dict(data)
    return None


def compute_vllm_admission_params(
    profile: VllmCalibrationProfile | None = None,
    rt_reservation: int = 2,
) -> VllmAdmissionParams:
    """Compute admission control parameters from a vLLM profile.

    If a calibration profile exists, uses the measured safe concurrency
    to set total_capacity and batch_max_inflight. Otherwise returns
    conservative defaults.

    Args:
        profile: Calibration profile, or None for defaults.
        rt_reservation: Minimum slots reserved for realtime sessions.

    Returns:
        VllmAdmissionParams with computed limits.
    """
    if profile is None:
        return VllmAdmissionParams(rt_reservation=rt_reservation)

    safe = profile.max_safe_concurrency
    optimal = profile.optimal_concurrency

    # Total capacity = max safe concurrency from calibration
    total_capacity = max(safe, rt_reservation + 1)

    # Batch can use capacity beyond RT reservation
    batch_max_inflight = max(1, total_capacity - rt_reservation)

    # Use the optimal concurrency as a hint — if it's lower than max safe,
    # it means throughput degrades before failures occur. Cap batch there.
    if optimal < safe:
        batch_max_inflight = min(batch_max_inflight, optimal)

    return VllmAdmissionParams(
        total_capacity=total_capacity,
        batch_max_inflight=batch_max_inflight,
        rt_reservation=rt_reservation,
        throughput_rps=profile.throughput_at_max,
        latency_per_audio_s=profile.latency_per_audio_s,
        profile_source="calibrated",
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


def get_gpu_name(gpu_index: int = 0) -> str | None:
    """Return the GPU display name via pynvml, or None if unavailable."""
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        name = pynvml.nvmlDeviceGetName(handle)
        pynvml.nvmlShutdown()
        return name.decode() if isinstance(name, bytes) else name
    except Exception:
        return None


def _get_gpu_total_mb() -> int:
    """Get total VRAM of GPU 0 in MB using pynvml, torch, or nvidia-smi."""
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
            return int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
    except Exception:
        pass

    # Fallback: nvidia-smi (works in containers without pynvml/torch)
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.split("\n")[0]))
    except Exception:
        pass

    return 0
