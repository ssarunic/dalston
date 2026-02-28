"""NeMo MSDD speaker diarization engine.

Uses NVIDIA NeMo Multi-scale Diarization Decoder for speaker diarization.
Produces speaker turns with overlap detection.

No HF_TOKEN required - models are open under CC-BY-4.0 license.

References:
- https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/configs.html
- https://github.com/NVIDIA/NeMo/blob/main/examples/speaker_tasks/diarization/neural_diarizer/multiscale_diar_decoder_infer.py
"""

import json
import os
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from dalston.engine_sdk import (
    DiarizeOutput,
    Engine,
    SpeakerTurn,
    TaskInput,
    TaskOutput,
)

# NeMo diarization config based on diar_infer_telephonic.yaml
# See: https://github.com/NVIDIA/NeMo/blob/main/examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml
DIAR_CONFIG = """
name: "NeuralDiarizer"
num_workers: 1
sample_rate: 16000
batch_size: 64
device: null
verbose: True

diarizer:
  manifest_filepath: null
  out_dir: null
  oracle_vad: False
  collar: 0.25
  ignore_overlap: True

  vad:
    model_path: vad_multilingual_marblenet
    external_vad_manifest: null
    parameters:
      window_length_in_sec: 0.15
      shift_length_in_sec: 0.01
      smoothing: median
      overlap: 0.5
      onset: 0.1
      offset: 0.1
      pad_onset: 0.1
      pad_offset: 0
      min_duration_on: 0
      min_duration_off: 0.2
      filter_speech_first: True

  speaker_embeddings:
    model_path: titanet_large
    parameters:
      window_length_in_sec: [1.5, 1.25, 1.0, 0.75, 0.5]
      shift_length_in_sec: [0.75, 0.625, 0.5, 0.375, 0.25]
      multiscale_weights: [1, 1, 1, 1, 1]
      save_embeddings: True

  clustering:
    parameters:
      oracle_num_speakers: False
      max_num_speakers: 8
      enhanced_count_thres: 80
      max_rp_threshold: 0.25
      sparse_search_volume: 30
      maj_vote_spk_count: False

  msdd_model:
    model_path: diar_msdd_telephonic
    parameters:
      use_speaker_model_from_ckpt: True
      infer_batch_size: 25
      sigmoid_threshold: [0.7]
      seq_eval_mode: False
      split_infer: True
      diar_window_length: 50
      overlap_infer_spk_limit: 5

  asr:
    model_path: null
    parameters:
      asr_based_vad: False
"""


class NemoMSDDEngine(Engine):
    """NeMo MSDD speaker diarization engine.

    Uses Multi-scale Diarization Decoder for end-to-end neural diarization
    with built-in overlap detection.

    Environment Variables:
        DIARIZATION_DISABLED: Set to "true" to skip diarization (returns mock output)
        DEVICE: Device to use ("cuda", "cpu", or unset for auto-detect)
        NEMO_ALLOW_CPU: Set to "true" to allow slow CPU inference
    """

    def __init__(self) -> None:
        super().__init__()
        self._diarizer = None
        self._device = self._detect_device()
        self._disabled = (
            os.environ.get("DALSTON_DIARIZATION_DISABLED", "").lower() == "true"
        )

        if self._disabled:
            self.logger.warning("diarization_disabled")
        else:
            self.logger.info("nemo_msdd_engine_initialized", device=self._device)

    def _detect_device(self) -> str:
        """Resolve inference device from DEVICE env with NeMo CPU guardrails."""
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
                message=(
                    "DEVICE=cpu set. NeMo MSDD on CPU is very slow "
                    "(recommended only for development/testing)."
                ),
            )
            return "cpu"

        if requested_device == "cuda":
            if not cuda_available:
                raise RuntimeError("DEVICE=cuda but CUDA is not available.")
            self.logger.info("cuda_available", device_count=cuda_device_count)
            return "cuda"

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DEVICE value: {requested_device}. Use cuda or cpu."
            )

        if cuda_available:
            self.logger.info(
                "cuda_available",
                device_count=cuda_device_count,
            )
            return "cuda"
        if allow_cpu:
            self.logger.warning(
                "cuda_not_available_using_cpu",
                message="CPU mode enabled via NEMO_ALLOW_CPU=true. "
                "Performance will be 10-50x slower than GPU.",
            )
            return "cpu"

        self.logger.error("cuda_not_available")
        raise RuntimeError(
            "NeMo MSDD requires GPU. CUDA is not available. "
            "Set DEVICE=cpu or NEMO_ALLOW_CPU=true to allow slow CPU inference, "
            "or use pyannote-4.0 for CPU-based diarization."
        )

    def _create_diarizer(
        self, manifest_path: str, out_dir: str, max_speakers: int | None
    ) -> Any:
        """Create NeuralDiarizer with configured paths.

        Per NeMo docs, the diarizer is created with a full config,
        not loaded via from_pretrained().
        """
        from nemo.collections.asr.models.msdd_models import NeuralDiarizer

        # Load base config
        cfg = OmegaConf.create(DIAR_CONFIG)

        # Set required paths
        cfg.diarizer.manifest_filepath = manifest_path
        cfg.diarizer.out_dir = out_dir
        cfg.device = self._device

        # Apply max_speakers if provided
        if max_speakers is not None:
            cfg.diarizer.clustering.parameters.max_num_speakers = max_speakers

        self.logger.info(
            "creating_neural_diarizer",
            manifest=manifest_path,
            out_dir=out_dir,
            device=self._device,
        )

        diarizer = NeuralDiarizer(cfg=cfg)
        return diarizer

    def _create_manifest(
        self, audio_path: Path, duration: float | None, out_dir: Path
    ) -> Path:
        """Create NeMo manifest file for diarization."""
        manifest_entry = {
            "audio_filepath": str(audio_path.resolve()),
            "offset": 0.0,
            "duration": duration,
            "label": "infer",
            "text": "-",
            "num_speakers": None,
            "rttm_filepath": None,
            "uem_filepath": None,
        }

        manifest_path = out_dir / f"{audio_path.stem}_manifest.json"
        with open(manifest_path, "w") as f:
            f.write(json.dumps(manifest_entry) + "\n")

        self.logger.debug("manifest_created", path=str(manifest_path))
        return manifest_path

    def _parse_rttm(self, rttm_path: Path) -> tuple[list[str], list[SpeakerTurn]]:
        """Parse RTTM output into speakers and turns.

        RTTM format:
        SPEAKER <session_id> <channel> <start> <duration> <NA> <NA> <speaker> <NA> <NA>
        """
        speakers_set: set[str] = set()
        turns: list[SpeakerTurn] = []

        with open(rttm_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 8 or parts[0] != "SPEAKER":
                    continue

                start = float(parts[3])
                duration = float(parts[4])
                speaker_id = parts[7]

                # Normalize speaker ID to SPEAKER_XX format
                if not speaker_id.startswith("SPEAKER_"):
                    speaker_num = "".join(c for c in speaker_id if c.isdigit())
                    speaker_id = f"SPEAKER_{speaker_num.zfill(2)}"

                speakers_set.add(speaker_id)
                turns.append(
                    SpeakerTurn(
                        start=round(start, 3),
                        end=round(start + duration, 3),
                        speaker=speaker_id,
                    )
                )

        speakers = sorted(speakers_set)
        turns.sort(key=lambda t: t.start)
        return speakers, turns

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

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Get audio duration using soundfile."""
        import soundfile as sf

        info = sf.info(str(audio_path))
        return info.duration

    def process(self, input: TaskInput) -> TaskOutput:
        """Run speaker diarization on audio file."""
        if self._disabled:
            self.logger.info("diarization_disabled_returning_mock_output")
            return self._mock_output()

        audio_path = input.audio_path
        config = input.config

        # Get duration
        duration: float | None = None
        try:
            prepare_output = input.get_prepare_output()
            if prepare_output and prepare_output.channel_files:
                duration = prepare_output.channel_files[0].duration
        except Exception:
            pass

        if duration is None:
            duration = self._get_audio_duration(audio_path)

        max_speakers = config.get("max_speakers")

        self.logger.info(
            "processing_diarization",
            audio_path=str(audio_path),
            duration=round(duration, 2),
            max_speakers=max_speakers,
        )

        # Create output directory and manifest
        out_dir = audio_path.parent / "nemo_diar_output"
        out_dir.mkdir(exist_ok=True)
        manifest_path = self._create_manifest(audio_path, duration, out_dir)

        try:
            # Create diarizer with this specific config
            diarizer = self._create_diarizer(
                manifest_path=str(manifest_path),
                out_dir=str(out_dir),
                max_speakers=max_speakers,
            )

            self.logger.info("running_nemo_diarization")
            diarizer.diarize()

            # Find RTTM output
            rttm_path = out_dir / "pred_rttms" / f"{audio_path.stem}.rttm"
            if not rttm_path.exists():
                rttm_path = out_dir / f"{audio_path.stem}.rttm"

            if not rttm_path.exists():
                raise RuntimeError(
                    f"RTTM output not found. Checked: {out_dir}/pred_rttms/ and {out_dir}/"
                )

            speakers, turns = self._parse_rttm(rttm_path)
            overlap_duration, overlap_ratio = self._calculate_overlap_stats(
                turns, duration
            )

        finally:
            manifest_path.unlink(missing_ok=True)

        self.logger.info(
            "diarization_complete",
            speaker_count=len(speakers),
            turn_count=len(turns),
            overlap_ratio=overlap_ratio,
        )

        output = DiarizeOutput(
            speakers=speakers,
            turns=turns,
            num_speakers=len(speakers),
            overlap_duration=overlap_duration,
            overlap_ratio=overlap_ratio,
            engine_id="nemo-msdd",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

    def _mock_output(self) -> TaskOutput:
        """Return mock output when diarization is disabled."""
        output = DiarizeOutput(
            speakers=["SPEAKER_00"],
            turns=[SpeakerTurn(start=0.0, end=999999.0, speaker="SPEAKER_00")],
            num_speakers=1,
            overlap_duration=0.0,
            overlap_ratio=0.0,
            engine_id="nemo-msdd",
            skipped=True,
            skip_reason="DIARIZATION_DISABLED=true",
            warnings=["Diarization disabled via environment variable"],
        )
        return TaskOutput(data=output)

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
        }


if __name__ == "__main__":
    engine = NemoMSDDEngine()
    engine.run()
