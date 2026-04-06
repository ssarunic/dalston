"""Integrated combo engine: HF ASR + Phoneme Align + Pyannote 4.0.

Performs transcription, word-level alignment, and speaker diarization
in a single process.  Returns a multi-key envelope for the batch pipeline
so that the standard ``assemble_transcript()`` merging logic handles
word-level speaker splitting.  For direct HTTP calls (``POST /v1/transcribe``),
returns a plain Transcript.

Execution order (sequential, shared GPU):
    1. Transcribe — HuggingFace ASR pipeline (Whisper, Wav2Vec2, etc.)
    2. Align — torchaudio wav2vec2 phoneme-level forced alignment
    3. Diarize — pyannote 4.0 speaker diarization

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID (default: "hf-asr-align-pyannote")
    DALSTON_DEFAULT_MODEL: Default HF ASR model (default: "openai/whisper-large-v3")
    DALSTON_DEVICE: Device for inference (cuda, mps, cpu; default: auto-detect)
    DALSTON_MODEL_TTL_SECONDS: Idle model eviction TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max ASR models loaded (default: 2)
    DALSTON_MODEL_PRELOAD: ASR model to preload on startup (optional)
    DALSTON_DEFAULT_DIARIZE_MODEL: Pyannote model (default: pyannote/speaker-diarization-community-1)
    DALSTON_MAX_DIARIZE_CHUNK_S: Max diarization chunk seconds (default: 3600)
    HF_TOKEN: HuggingFace token for pyannote gated models
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
from align import AlignedSegment, InputSegment, align
from model_loader import AlignModelMetadata, load_align_model

from dalston.common.pipeline_types import (
    AlignmentMethod,
    AlignmentResponse,
    DiarizationResponse,
    Segment,
    SpeakerTurn,
    TimestampGranularity,
    Transcript,
    Word,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    TaskRequest,
    TaskResponse,
    detect_device,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.diarize_chunking import (
    DEFAULT_MAX_CHUNK_S,
    get_audio_duration,
    overlap_stats_from_turns,
    run_chunked_diarization,
)
from dalston.engine_sdk.managers import HFTransformersModelManager


class HfAsrAlignPyannoteEngine(BaseBatchTranscribeEngine):
    """Integrated transcription + alignment + diarization engine.

    For batch pipeline tasks: runs all three stages sequentially and
    returns a multi-key envelope with ``stages_completed`` so the
    orchestrator can unpack into separate stage outputs.

    For direct HTTP calls: runs transcription only and returns a plain
    ``Transcript`` (OpenAI-compatible).
    """

    ENGINE_ID = "hf-asr-align-pyannote"
    DEFAULT_MODEL = "openai/whisper-large-v3"
    DEFAULT_DIARIZE_MODEL = "pyannote/speaker-diarization-community-1"

    def __init__(self, manager: HFTransformersModelManager | None = None) -> None:
        super().__init__()

        self._device = detect_device()
        self._torch_dtype = (
            torch.float16 if self._device in ("cuda", "mps") else torch.float32
        )

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL", self.DEFAULT_MODEL
        )
        self._default_diarize_model = os.environ.get(
            "DALSTON_DEFAULT_DIARIZE_MODEL", self.DEFAULT_DIARIZE_MODEL
        )
        self._max_chunk_s = float(
            os.environ.get("DALSTON_MAX_DIARIZE_CHUNK_S", DEFAULT_MAX_CHUNK_S)
        )

        # ASR model manager (TTL/LRU lifecycle)
        if manager is not None:
            self._asr_manager = manager
        else:
            from dalston.engine_sdk.model_storage import MultiSourceModelStorage

            model_storage = MultiSourceModelStorage.from_env()
            self._asr_manager = HFTransformersModelManager(
                device=self._device,
                torch_dtype=self._torch_dtype,
                model_storage=model_storage,
                ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
                max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
                preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            )

        # Alignment model cache (small models, no eviction needed)
        self._align_models: dict[str, tuple[Any, AlignModelMetadata]] = {}

        # Pyannote pipeline cache
        self._diarize_pipelines: dict[str, Any] = {}

        self.logger.info(
            "combo_engine_init",
            engine_id=self.engine_id,
            device=self._device,
            default_asr_model=self._default_model_id,
            default_diarize_model=self._default_diarize_model,
        )

    def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        """Run the engine.

        HTTP direct mode (job_id == "http"): transcription only, returns
        a plain Transcript.

        Batch pipeline mode: runs transcribe -> align -> diarize and
        returns a multi-key envelope for assemble_transcript().
        """
        if task_request.job_id == "http":
            transcript = self.transcribe_audio(task_request, ctx)
            return TaskResponse(data=transcript)

        return self._run_full_pipeline(task_request, ctx)

    def transcribe_audio(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio using HuggingFace ASR pipeline."""
        return self._run_transcribe(task_request)

    def _run_full_pipeline(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> TaskResponse:
        """Run all three stages and return multi-key envelope."""
        self.logger.info("combo_stage_transcribe_start")
        transcript = self._run_transcribe(task_request)
        self.logger.info(
            "combo_stage_transcribe_done",
            segments=len(transcript.segments),
            chars=len(transcript.text),
        )

        self.logger.info("combo_stage_align_start")
        align_response = self._run_align(task_request.audio_path, transcript)
        self.logger.info(
            "combo_stage_align_done",
            skipped=align_response.skipped,
            segments=len(align_response.segments),
        )

        self.logger.info("combo_stage_diarize_start")
        diarize_response = self._run_diarize(task_request)
        self.logger.info(
            "combo_stage_diarize_done",
            skipped=diarize_response.skipped,
            speakers=len(diarize_response.speakers),
            turns=len(diarize_response.turns),
        )

        # Build multi-key envelope
        stages_completed = ["transcribe"]
        if not align_response.skipped:
            stages_completed.append("align")
        if not diarize_response.skipped:
            stages_completed.append("diarize")

        envelope: dict[str, Any] = {
            "stages_completed": stages_completed,
            "transcribe": transcript.model_dump(mode="json"),
            "align": align_response.model_dump(mode="json"),
            "diarize": diarize_response.model_dump(mode="json"),
        }

        self.logger.info(
            "combo_pipeline_complete",
            stages_completed=stages_completed,
        )

        return TaskResponse(data=envelope)

    def _run_transcribe(self, task_request: TaskRequest) -> Transcript:
        """Run HuggingFace ASR pipeline."""
        params = task_request.get_transcribe_params()
        loaded_model_id = params.loaded_model_id or self._default_model_id

        language = params.language
        if language == "auto" or language == "":
            language = None

        channel = params.channel

        hf_default = int(os.environ.get("DALSTON_HF_BATCH_SIZE", "1"))
        if params.vad_batch_size is not None:
            adaptive_batch_size = params.vad_batch_size
        else:
            adaptive_batch_size = self._resolve_adaptive_batch_size(fallback=hf_default)

        pipe = self._asr_manager.acquire(loaded_model_id)
        try:
            self._set_runtime_state(loaded_model=loaded_model_id, status="processing")
            self.logger.info(
                "transcribing",
                audio_path=str(task_request.audio_path),
                loaded_model_id=loaded_model_id,
                language=language,
                batch_size=adaptive_batch_size,
            )

            pipe_kwargs: dict[str, Any] = {}
            if adaptive_batch_size > 1:
                pipe_kwargs["batch_size"] = adaptive_batch_size
            pipe_kwargs["return_timestamps"] = "word"

            generate_kwargs: dict[str, Any] = {}
            if language:
                generate_kwargs["language"] = language

            vocabulary = params.vocabulary
            if vocabulary and hasattr(pipe.tokenizer, "get_prompt_ids"):
                prompt_text = ", ".join(vocabulary)
                prompt_ids = pipe.tokenizer.get_prompt_ids(prompt_text)
                generate_kwargs["prompt_ids"] = prompt_ids

            if generate_kwargs:
                pipe_kwargs["generate_kwargs"] = generate_kwargs

            result = pipe(str(task_request.audio_path), **pipe_kwargs)

            return self._normalize_asr_output(
                result, loaded_model_id, language, channel
            )
        finally:
            self._asr_manager.release(loaded_model_id)
            self._set_runtime_state(status="idle")

    def _normalize_asr_output(
        self,
        result: dict[str, Any],
        model_id: str,
        language: str | None,
        channel: int | None,
    ) -> Transcript:
        """Normalize HuggingFace pipeline output to Transcript."""
        text = result.get("text", "").strip()
        chunks = result.get("chunks", [])
        segments = []
        has_word_timestamps = False

        if chunks:
            words = []
            for chunk in chunks:
                chunk_text = chunk.get("text", "").strip()
                if not chunk_text:
                    continue
                timestamp = chunk.get("timestamp", (None, None))
                start = timestamp[0] if timestamp and timestamp[0] is not None else 0.0
                end = timestamp[1] if timestamp and timestamp[1] is not None else 0.0
                words.append(
                    self.build_word(
                        text=chunk_text,
                        start=round(start, 3),
                        end=round(end, 3),
                        confidence=None,
                        alignment_method=AlignmentMethod.ATTENTION,
                    )
                )
            has_word_timestamps = bool(words)
            if words:
                segments.append(
                    self.build_segment(
                        start=round(words[0].start, 3),
                        end=round(words[-1].end, 3),
                        text=text,
                        words=words,
                    )
                )
            else:
                segments.append(self.build_segment(start=0.0, end=0.0, text=text))
        else:
            segments.append(self.build_segment(start=0.0, end=0.0, text=text))

        return self.build_transcript(
            text=text,
            segments=segments,
            language=language or "auto",
            engine_id=self.engine_id,
            alignment_method=(
                AlignmentMethod.ATTENTION
                if has_word_timestamps
                else AlignmentMethod.UNKNOWN
            ),
            channel=channel,
        )

    def _run_align(
        self, audio_path: Path | None, transcript: Transcript
    ) -> AlignmentResponse:
        """Run phoneme-level forced alignment on transcription output."""
        if audio_path is None:
            return self._align_fallback(transcript, reason="No audio path available")

        language = transcript.language or "en"
        # HF-ASR often returns one giant segment spanning the entire audio.
        # wav2vec2 alignment OOMs on long segments (~6 GiB for 4 min audio).
        # Split into ~30s chunks at word boundaries before alignment.
        raw_segments = self._split_long_segments(transcript.segments)
        segment_languages = [getattr(s, "language", None) for s in transcript.segments]
        # Extend languages to match split segment count
        if len(segment_languages) == 1 and len(raw_segments) > 1:
            segment_languages = [segment_languages[0]] * len(raw_segments)

        model_result = self._get_align_model(language, loaded_model_id=None)
        if model_result is None:
            return self._align_fallback(
                transcript,
                reason=f"Failed to load alignment model for language '{language}'",
            )

        model, metadata = model_result

        self._set_runtime_state(loaded_model="phoneme-align", status="processing")
        try:
            audio = self._load_audio(audio_path)

            result = align(
                transcript=raw_segments,
                model=model,
                metadata=metadata,
                audio=audio,
                device=self._device,
                return_char_alignments=False,
            )

            output_segments, stats = self._to_sdk_segments(
                result.segments, segment_languages
            )

            all_words: list[Word] = []
            for seg in output_segments:
                if seg.words:
                    all_words.extend(seg.words)

            confidences = [w.confidence for w in all_words if w.confidence is not None]
            alignment_confidence = (
                sum(confidences) / len(confidences) if confidences else None
            )

            aligned_count = len(all_words)
            unaligned_count = stats["unaligned_words"]
            total_count = aligned_count + unaligned_count

            return AlignmentResponse(
                text=transcript.text,
                segments=output_segments,
                language=language,
                word_timestamps=True,
                alignment_confidence=(
                    round(alignment_confidence, 3)
                    if alignment_confidence is not None
                    else None
                ),
                unaligned_words=[f"word_{i}" for i in range(unaligned_count)],
                unaligned_ratio=(
                    round(unaligned_count / total_count, 3) if total_count > 0 else 0.0
                ),
                granularity_achieved=TimestampGranularity.WORD,
                engine_id="phoneme-align",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

        except Exception as e:
            self.logger.error("alignment_failed", error=str(e), exc_info=True)
            return self._align_fallback(transcript, reason=f"Alignment failed: {e}")
        finally:
            self._set_runtime_state(status="idle")

    def _split_long_segments(
        self,
        segments: list,
        max_duration_s: float = 30.0,
    ) -> list[InputSegment]:
        """Split long segments into ~max_duration_s chunks at word boundaries.

        HF-ASR often returns one segment spanning the entire audio.
        wav2vec2 alignment OOMs on long segments, so we split using
        word timestamps (from Whisper's attention-based alignment) as
        natural cut points.
        """
        result: list[InputSegment] = []

        for seg in segments:
            duration = seg.end - seg.start
            words = getattr(seg, "words", None) or []

            if duration <= max_duration_s or not words:
                result.append(InputSegment(start=seg.start, end=seg.end, text=seg.text))
                continue

            # Split at word boundaries every ~max_duration_s
            chunk_words: list = []
            chunk_start = seg.start

            for word in words:
                chunk_words.append(word)
                chunk_end = word.end
                if chunk_end - chunk_start >= max_duration_s:
                    text = " ".join(w.text for w in chunk_words).strip()
                    if text:
                        result.append(
                            InputSegment(
                                start=round(chunk_start, 3),
                                end=round(chunk_end, 3),
                                text=text,
                            )
                        )
                    chunk_words = []
                    chunk_start = chunk_end

            # Remaining words
            if chunk_words:
                text = " ".join(w.text for w in chunk_words).strip()
                if text:
                    result.append(
                        InputSegment(
                            start=round(chunk_start, 3),
                            end=round(seg.end, 3),
                            text=text,
                        )
                    )

        self.logger.info(
            "segments_split_for_alignment",
            original_count=len(segments),
            split_count=len(result),
            max_duration_s=max_duration_s,
        )
        return result

    def _get_align_model(
        self, language: str, loaded_model_id: str | None
    ) -> tuple[Any, AlignModelMetadata] | None:
        """Load or retrieve a cached alignment model."""
        cache_key = loaded_model_id or f"_default_{language}"
        if cache_key in self._align_models:
            return self._align_models[cache_key]

        self.logger.info(
            "loading_alignment_model",
            language=language,
            device=self._device,
            loaded_model_id=loaded_model_id,
        )
        try:
            model, metadata = load_align_model(
                language_code=language,
                device=self._device,
                model_name=loaded_model_id,
            )
            self._align_models[cache_key] = (model, metadata)
            return model, metadata
        except Exception as e:
            self.logger.warning(
                "failed_to_load_alignment_model",
                language=language,
                error=str(e),
            )
            return None

    def _load_audio(self, audio_path: Path) -> np.ndarray:
        """Load audio file as float32 numpy array, resampled to 16 kHz mono."""
        data, sr = sf.read(str(audio_path), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            import torchaudio.functional as F

            data_tensor = torch.from_numpy(data)
            data_tensor = F.resample(data_tensor, sr, 16000)
            data = data_tensor.numpy()
        return data

    def _to_sdk_segments(
        self,
        aligned_segments: list[AlignedSegment],
        segment_languages: list[str | None] | None = None,
    ) -> tuple[list[Segment], dict[str, int]]:
        """Convert aligned segments to SDK types."""
        segments: list[Segment] = []
        unaligned_words = 0

        for idx, aseg in enumerate(aligned_segments):
            seg_language: str | None = None
            if segment_languages and idx < len(segment_languages):
                seg_language = segment_languages[idx]

            words: list[Word] | None = None
            if aseg.words:
                valid_words: list[Word] = []
                for aw in aseg.words:
                    if not aw.word.strip():
                        continue
                    if aw.start is None or aw.end is None:
                        unaligned_words += 1
                        continue
                    valid_words.append(
                        Word(
                            text=aw.word,
                            start=aw.start,
                            end=aw.end,
                            confidence=aw.score,
                            alignment_method=AlignmentMethod.PHONEME_WAV2VEC,
                            language=seg_language,
                        )
                    )
                words = valid_words if valid_words else None

            segments.append(
                Segment(
                    start=aseg.start,
                    end=aseg.end,
                    text=aseg.text,
                    words=words,
                    language=seg_language,
                )
            )

        return segments, {"unaligned_words": unaligned_words}

    def _align_fallback(self, transcript: Transcript, reason: str) -> AlignmentResponse:
        """Return original timestamps when alignment is not possible."""
        self.logger.warning(
            "alignment_fallback",
            reason=reason,
            segment_count=len(transcript.segments),
        )
        typed_segments = [
            Segment(start=s.start, end=s.end, text=s.text) for s in transcript.segments
        ]
        return AlignmentResponse(
            text=transcript.text,
            segments=typed_segments,
            language=transcript.language or "en",
            word_timestamps=False,
            alignment_confidence=None,
            unaligned_words=[],
            unaligned_ratio=0.0,
            granularity_achieved=TimestampGranularity.SEGMENT,
            engine_id="phoneme-align",
            skipped=True,
            skip_reason=reason,
            warnings=[reason],
        )

    def _run_diarize(self, task_request: TaskRequest) -> DiarizationResponse:
        """Run pyannote speaker diarization."""
        audio_path = task_request.audio_path
        if audio_path is None:
            return self._diarize_skipped("No audio path available")

        params = task_request.get_diarize_params()
        # The shared config may have loaded_model_id set to the ASR model
        # (e.g. "openai/whisper-base"). Use the diarize-specific config key
        # or fall back to the default pyannote model.
        loaded_model_id = (
            task_request.config.get("diarize_model_id") or self._default_diarize_model
        )

        min_speakers = params.min_speakers
        max_speakers = params.max_speakers
        exclusive = params.exclusive

        try:
            hf_token = self._get_hf_token(task_request.config)
        except RuntimeError as e:
            return self._diarize_skipped(str(e))

        pipeline = self._load_diarize_pipeline(loaded_model_id, hf_token)
        self._set_runtime_state(loaded_model=loaded_model_id, status="processing")
        try:
            speakers: list[str] = []
            turns: list[SpeakerTurn] = []
            overlap_duration = 0.0
            overlap_ratio = 0.0

            diarization_params: dict[str, Any] = {}
            if min_speakers is not None:
                diarization_params["min_speakers"] = min_speakers
            if max_speakers is not None:
                diarization_params["max_speakers"] = max_speakers

            duration = get_audio_duration(audio_path)

            from dalston.engine_sdk.inference.gpu_guard import (
                clear_gpu_cache,
                is_oom_error,
            )

            max_chunk_s = self._max_chunk_s
            use_chunked = duration > max_chunk_s

            if not use_chunked:
                try:
                    diarization = pipeline(str(audio_path), **diarization_params)
                    if exclusive and hasattr(
                        diarization, "exclusive_speaker_diarization"
                    ):
                        diarization = diarization.exclusive_speaker_diarization
                    speakers, turns = self._convert_annotation(diarization)
                    overlap_duration, overlap_ratio = self._calculate_overlap_stats(
                        diarization
                    )
                except Exception as exc:
                    if not is_oom_error(exc):
                        raise
                    clear_gpu_cache()
                    max_chunk_s = duration / 2
                    use_chunked = True

            if use_chunked:
                while max_chunk_s >= 30:
                    try:
                        speakers, turns = run_chunked_diarization(
                            pipeline,
                            audio_path,
                            diarization_params,
                            hf_token=hf_token,
                            device=self._device,
                            convert_annotation=self._convert_annotation,
                            exclusive=bool(exclusive),
                            max_chunk_s=max_chunk_s,
                            log=self.logger,
                        )
                        break
                    except RuntimeError as exc:
                        if "All chunks failed" not in str(exc) or max_chunk_s <= 30:
                            raise
                        clear_gpu_cache()
                        max_chunk_s = max(30, max_chunk_s / 2)

                overlap_duration, overlap_ratio = overlap_stats_from_turns(turns)

            self.logger.info(
                "diarization_complete",
                speaker_count=len(speakers),
                turn_count=len(turns),
            )

            return DiarizationResponse(
                speakers=speakers,
                turns=turns,
                num_speakers=len(speakers),
                overlap_duration=round(overlap_duration, 3),
                overlap_ratio=round(overlap_ratio, 3),
                engine_id="pyannote-4.0",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

        except Exception as e:
            self.logger.error("diarization_failed", error=str(e), exc_info=True)
            return self._diarize_skipped(f"Diarization failed: {e}")
        finally:
            self._set_runtime_state(status="idle")

    def _load_diarize_pipeline(self, model_id: str, hf_token: str) -> Any:
        """Load pyannote pipeline lazily."""
        if model_id in self._diarize_pipelines:
            return self._diarize_pipelines[model_id]

        self.logger.info("loading_pyannote_pipeline", model_id=model_id)

        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained(model_id, token=hf_token, revision="main")

        if self._device in ("cuda", "mps"):
            pipeline = pipeline.to(torch.device(self._device))

        self._diarize_pipelines[model_id] = pipeline
        return pipeline

    def _get_hf_token(self, config: dict[str, Any]) -> str:
        """Get HuggingFace token from config or environment."""
        token = config.get("hf_token") or os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "HF_TOKEN is required for pyannote diarization. "
                "Get a token from https://huggingface.co/settings/tokens "
                "and accept the pyannote model agreement."
            )
        return token

    def _convert_annotation(
        self, diarization: Any
    ) -> tuple[list[str], list[SpeakerTurn]]:
        """Convert pyannote diarization output to speakers and turns."""
        speakers_set: set[str] = set()
        turns: list[SpeakerTurn] = []

        if hasattr(diarization, "speaker_diarization"):
            annotation = diarization.speaker_diarization
        else:
            annotation = diarization

        for turn, _, speaker in annotation.itertracks(yield_label=True):
            speakers_set.add(speaker)
            turns.append(
                SpeakerTurn(
                    start=round(turn.start, 3),
                    end=round(turn.end, 3),
                    speaker=speaker,
                )
            )

        speakers = sorted(speakers_set)
        turns.sort(key=lambda t: t.start)
        return speakers, turns

    def _calculate_overlap_stats(self, diarization: Any) -> tuple[float, float]:
        """Calculate overlap statistics from pyannote output."""
        try:
            if hasattr(diarization, "speaker_diarization"):
                annotation = diarization.speaker_diarization
            else:
                annotation = diarization

            overlap_timeline = annotation.get_overlap()
            overlap_duration = (
                sum(segment.duration for segment in overlap_timeline)
                if overlap_timeline
                else 0.0
            )
            total_duration = (
                annotation.get_timeline().duration()
                if annotation.get_timeline()
                else 0.0
            )
            overlap_ratio = (
                overlap_duration / total_duration if total_duration > 0 else 0.0
            )
            return overlap_duration, overlap_ratio
        except Exception:
            return 0.0, 0.0

    def _diarize_skipped(self, reason: str) -> DiarizationResponse:
        """Return a skipped diarization response."""
        self.logger.warning("diarization_skipped", reason=reason)
        return DiarizationResponse(
            speakers=[],
            turns=[],
            num_speakers=0,
            overlap_duration=0.0,
            overlap_ratio=0.0,
            engine_id="pyannote-4.0",
            skipped=True,
            skip_reason=reason,
            warnings=[reason],
        )

    def health_check(self) -> dict[str, Any]:
        return {
            **super().health_check(),
            "device": self._device,
            "torch_dtype": str(self._torch_dtype),
            "asr_model_manager": self._asr_manager.get_stats(),
            "cached_align_models": sorted(self._align_models.keys()),
            "loaded_diarize_pipelines": sorted(self._diarize_pipelines.keys()),
        }

    def shutdown(self) -> None:
        self.logger.info("combo_engine_shutdown")
        self._asr_manager.shutdown()
        self._align_models.clear()
        self._diarize_pipelines.clear()
        super().shutdown()


if __name__ == "__main__":
    engine = HfAsrAlignPyannoteEngine()
    engine.run()
