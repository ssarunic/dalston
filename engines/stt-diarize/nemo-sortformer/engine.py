"""NeMo Sortformer speaker diarization engine.

Uses NVIDIA NeMo SortformerEncLabelModel for end-to-end neural speaker
diarization.  The Sortformer architecture resolves the permutation problem
by sorting speakers in arrival-time order and natively handles overlapping
speech.

Supports up to 4 speakers.  No HF_TOKEN required — models are open under
NVIDIA Open Model License.

References:
- https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1
- https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/models.html
"""

import os
from pathlib import Path
from typing import Any

from dalston.engine_sdk import (
    BatchTaskContext,
    DiarizationResponse,
    Engine,
    SpeakerTurn,
    TaskRequest,
    TaskResponse,
)

# Runtime model definitions for NeMo Sortformer diarization.
# Each loaded_model_id maps to a HuggingFace model name used by
# SortformerEncLabelModel.from_pretrained().
MODEL_REGISTRY: dict[str, str] = {
    "nvidia/diar-sortformer-4spk-v2.1": "nvidia/diar_streaming_sortformer_4spk-v2.1",
    "nvidia/diar-sortformer-4spk-v2": "nvidia/diar_streaming_sortformer_4spk-v2",
    "nvidia/diar-sortformer-4spk-v1": "nvidia/diar_sortformer_4spk-v1",
}

DEFAULT_MODEL_ID = "nvidia/diar-sortformer-4spk-v2.1"


class NemoSortformerEngine(Engine):
    """NeMo Sortformer speaker diarization engine.

    End-to-end Transformer encoder model that directly predicts speaker
    labels from audio.  Supports up to 4 speakers with native overlap
    handling and low DER.

    Environment Variables:
        DALSTON_DIARIZATION_DISABLED: Set to "true" to skip diarization
        DALSTON_DEVICE: Device to use ("cuda", "cpu", or unset for auto)
        DALSTON_NEMO_ALLOW_CPU: Set to "true" to allow slow CPU inference
    """

    def __init__(self) -> None:
        super().__init__()
        self._models: dict[str, Any] = {}
        self._active_loaded_model_id: str | None = None
        self._device = self._detect_device()
        self._disabled = (
            os.environ.get("DALSTON_DIARIZATION_DISABLED", "").lower() == "true"
        )

        if self._disabled:
            self.logger.warning("diarization_disabled")
        else:
            self.logger.info("nemo_sortformer_engine_initialized", device=self._device)

    # ------------------------------------------------------------------
    # Device detection (shared pattern with nemo-msdd)
    # ------------------------------------------------------------------

    def _detect_device(self) -> str:
        """Resolve inference device from DALSTON_DEVICE env."""
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        allow_cpu = os.environ.get("DALSTON_NEMO_ALLOW_CPU", "").lower() == "true"

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            cuda_available = False
            cuda_device_count = 0

        if requested_device == "cpu":
            self.logger.warning(
                "device_forced_cpu",
                message="DALSTON_DEVICE=cpu set. Sortformer on CPU is very slow.",
            )
            return "cpu"

        if requested_device == "cuda":
            if not cuda_available:
                raise RuntimeError("DALSTON_DEVICE=cuda but CUDA is not available.")
            self.logger.info("cuda_available", device_count=cuda_device_count)
            return "cuda"

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DALSTON_DEVICE value: {requested_device}. Use cuda or cpu."
            )

        if cuda_available:
            self.logger.info("cuda_available", device_count=cuda_device_count)
            return "cuda"
        if allow_cpu:
            self.logger.warning(
                "cuda_not_available_using_cpu",
                message="CPU mode enabled via DALSTON_NEMO_ALLOW_CPU=true. "
                "Performance will be 10-50x slower than GPU.",
            )
            return "cpu"

        self.logger.error("cuda_not_available")
        raise RuntimeError(
            "NeMo Sortformer requires GPU. CUDA is not available. "
            "Set DALSTON_DEVICE=cpu or DALSTON_NEMO_ALLOW_CPU=true to allow "
            "slow CPU inference."
        )

    # ------------------------------------------------------------------
    # Model loading (lazy, cached per loaded_model_id)
    # ------------------------------------------------------------------

    def _resolve_hf_model(self, loaded_model_id: str) -> str:
        """Map a Dalston loaded_model_id to a HuggingFace model name."""
        if loaded_model_id not in MODEL_REGISTRY:
            raise ValueError(
                f"Unsupported loaded_model_id for nemo-sortformer: "
                f"{loaded_model_id}. "
                f"Known values: {sorted(MODEL_REGISTRY.keys())}"
            )
        return MODEL_REGISTRY[loaded_model_id]

    def _load_model(self, loaded_model_id: str) -> Any:
        """Load SortformerEncLabelModel lazily and cache it."""
        if loaded_model_id in self._models:
            return self._models[loaded_model_id]

        hf_model = self._resolve_hf_model(loaded_model_id)
        self.logger.info(
            "loading_sortformer_model",
            loaded_model_id=loaded_model_id,
            hf_model=hf_model,
        )

        from nemo.collections.asr.models import SortformerEncLabelModel

        model = SortformerEncLabelModel.from_pretrained(hf_model)

        if self._device == "cuda":
            model = model.cuda()

        model.eval()

        self._models[loaded_model_id] = model
        self.logger.info("sortformer_model_loaded", loaded_model_id=loaded_model_id)
        return model

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        """Run speaker diarization on audio file."""
        if self._disabled:
            self.logger.info("diarization_disabled_returning_mock_output")
            return self._mock_output()

        audio_path = task_request.audio_path
        config = task_request.config

        loaded_model_id = config.get("loaded_model_id")
        if not loaded_model_id:
            raise ValueError(
                "Missing required config field 'loaded_model_id' for diarize stage."
            )

        max_speakers = config.get("max_speakers")
        if max_speakers is not None and max_speakers > 4:
            raise ValueError(
                f"Sortformer supports at most 4 speakers, but max_speakers={max_speakers}. "
                "Use pyannote-4.0 or nemo-msdd for >4 speaker scenarios."
            )

        # Get audio duration for overlap stats
        duration = self._get_audio_duration(audio_path)

        self.logger.info(
            "processing_diarization",
            audio_path=str(audio_path),
            duration=round(duration, 2),
            max_speakers=max_speakers,
            loaded_model_id=loaded_model_id,
        )

        model = self._load_model(loaded_model_id)
        self._active_loaded_model_id = loaded_model_id
        self._set_runtime_state(loaded_model=loaded_model_id, status="processing")

        try:
            self.logger.info("running_sortformer_diarization")
            segments = model.diarize(audio=[str(audio_path)], batch_size=1)[0]

            # Parse diarize() output: each segment is a string "start end speaker_N"
            speakers_set: set[str] = set()
            turns: list[SpeakerTurn] = []

            for seg_str in segments:
                parts = seg_str.split()
                if len(parts) != 3:
                    raise ValueError(
                        f"Unexpected NeMo segment format (expected 3 fields): {seg_str!r}"
                    )
                start_s, end_s, spk_label = parts
                spk_idx = int(spk_label.split("_")[-1])
                speaker = f"SPEAKER_{spk_idx:02d}"
                speakers_set.add(speaker)
                turns.append(
                    SpeakerTurn(
                        start=round(float(start_s), 3),
                        end=round(float(end_s), 3),
                        speaker=speaker,
                    )
                )

            turns.sort(key=lambda t: t.start)
            speakers = sorted(speakers_set)

            overlap_duration, overlap_ratio = self._calculate_overlap_stats(
                turns, duration
            )

            self.logger.info(
                "diarization_complete",
                speaker_count=len(speakers),
                turn_count=len(turns),
                overlap_ratio=overlap_ratio,
            )

            output = DiarizationResponse(
                speakers=speakers,
                turns=turns,
                num_speakers=len(speakers),
                overlap_duration=overlap_duration,
                overlap_ratio=overlap_ratio,
                engine_id="nemo-sortformer",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskResponse(data=output)
        finally:
            self._set_runtime_state(loaded_model=loaded_model_id, status="idle")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Get audio duration using soundfile."""
        import soundfile as sf

        info = sf.info(str(audio_path))
        return info.duration

    def _calculate_overlap_stats(
        self, turns: list[SpeakerTurn], total_duration: float
    ) -> tuple[float, float]:
        """Calculate overlap duration and ratio using sweep-line algorithm."""
        if not turns or total_duration <= 0:
            return 0.0, 0.0

        events: list[tuple[float, int]] = []
        for turn in turns:
            events.append((turn.start, 1))
            events.append((turn.end, -1))

        events.sort(key=lambda e: (e[0], e[1]))

        overlap_duration = 0.0
        active_speakers = 0
        overlap_start: float | None = None

        for time, delta in events:
            if active_speakers >= 2 and overlap_start is not None:
                overlap_duration += time - overlap_start
                overlap_start = None

            active_speakers += delta

            if active_speakers >= 2 and overlap_start is None:
                overlap_start = time

        overlap_ratio = overlap_duration / total_duration if total_duration > 0 else 0.0
        return round(overlap_duration, 3), round(overlap_ratio, 4)

    def _mock_output(self) -> TaskResponse:
        """Return mock output when diarization is disabled."""
        output = DiarizationResponse(
            speakers=["SPEAKER_00"],
            turns=[SpeakerTurn(start=0.0, end=999999.0, speaker="SPEAKER_00")],
            num_speakers=1,
            overlap_duration=0.0,
            overlap_ratio=0.0,
            engine_id="nemo-sortformer",
            skipped=True,
            skip_reason="DIARIZATION_DISABLED=true",
            warnings=["Diarization disabled via environment variable"],
        )
        return TaskResponse(data=output)

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        cuda_available = False
        try:
            import torch

            cuda_available = torch.cuda.is_available()
        except ImportError:
            pass

        return {
            "status": "healthy" if cuda_available or self._disabled else "unhealthy",
            "device": getattr(self, "_device", "unknown"),
            "cuda_available": cuda_available,
            "diarization_disabled": self._disabled,
            "active_model_id": self._active_loaded_model_id,
            "loaded_models": sorted(self._models.keys()),
            "available_loaded_models": sorted(MODEL_REGISTRY.keys()),
            "max_speakers": 4,
        }


if __name__ == "__main__":
    engine = NemoSortformerEngine()
    engine.run()
