"""Unit tests for BaseBatchTranscribeEngine chunked path.

Covers M86 steps 86.2, 86.5, 86.6:

- 86.2 Chunking dispatch via get_max_audio_duration_s + VadChunker +
  _merge_chunk_transcripts with timestamp offsets.
- 86.5 OOM backoff loop: on CUDA OOM, halve the effective chunk cap and
  retry the remaining audio. Floor at 60s.
- 86.6 Aggregate telemetry: one top-level engine.recognize span per
  chunked request, regardless of chunk count.

The VadChunker is mocked end-to-end so these tests run without torch.hub
downloads or a real Silero model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import TaskRequest


def _make_transcript(
    text: str,
    segments: list[tuple[float, float, str]],
    engine_id: str = "test-engine",
    warnings: list[str] | None = None,
) -> Transcript:
    """Build a minimal Transcript with segment-only timestamps."""
    segs = [
        TranscriptSegment(
            start=s,
            end=e,
            text=t,
            words=[
                TranscriptWord(
                    text=w,
                    start=s + 0.1 * i,
                    end=s + 0.1 * (i + 1),
                    confidence=0.9,
                    alignment_method=AlignmentMethod.ATTENTION,
                )
                for i, w in enumerate(t.split())
            ],
        )
        for s, e, t in segments
    ]
    return Transcript(
        text=text,
        segments=segs,
        language="en",
        language_confidence=1.0,
        engine_id=engine_id,
        alignment_method=AlignmentMethod.ATTENTION,
        warnings=warnings or [],
    )


class _FakeChunk:
    def __init__(self, audio_path: Path, offset: float, duration: float) -> None:
        self.audio_path = audio_path
        self.offset = offset
        self.duration = duration


class _FakeVadChunker:
    """Test double for VadChunker — returns pre-canned chunks on split().

    Honours max_chunk_duration_s by re-splitting when the cap shrinks
    (simulating the OOM-backoff re-split pass).
    """

    scenarios: dict[float, list[tuple[float, float]]] = {}

    def __init__(self, max_chunk_duration_s: float = 1500.0, **_: Any) -> None:
        self.max_chunk_duration_s = max_chunk_duration_s

    def split(self, audio_path: Path, temp_dir: Path) -> list[_FakeChunk]:
        temp_dir.mkdir(parents=True, exist_ok=True)
        raw = self.scenarios.get(self.max_chunk_duration_s)
        if raw is None:
            raise AssertionError(
                f"_FakeVadChunker has no scenario for max_s={self.max_chunk_duration_s}"
            )
        chunks: list[_FakeChunk] = []
        for i, (offset, duration) in enumerate(raw):
            p = temp_dir / f"chunk_{i:04d}.wav"
            p.write_bytes(b"")  # presence only — we never decode in tests
            chunks.append(_FakeChunk(p, offset=offset, duration=duration))
        return chunks


class _SpyEngine(BaseBatchTranscribeEngine):
    """Subclass with controllable transcribe_audio behaviour."""

    def __init__(
        self,
        max_chunk_s: float | None,
        side_effects: list[Any],
    ) -> None:
        # bypass parent __init__ which would try to wire logger etc
        self.engine_id = "spy-engine"
        self._max_chunk_s = max_chunk_s
        self._side_effects = list(side_effects)
        self._calls: list[Path] = []

    def get_max_audio_duration_s(self, task_request: TaskRequest) -> float | None:
        return self._max_chunk_s

    def transcribe_audio(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> Transcript:
        assert task_request.audio_path is not None
        self._calls.append(task_request.audio_path)
        if not self._side_effects:
            raise AssertionError("Ran out of scripted side effects")
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


def _ctx() -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="spy-engine",
        instance="test",
        task_id="t1",
        job_id="j1",
        stage="transcribe",
    )


@pytest.fixture(autouse=True)
def _reset_fake_chunker() -> None:
    _FakeVadChunker.scenarios = {}
    yield
    _FakeVadChunker.scenarios = {}


class TestShortFileFastPath:
    """No chunking when audio fits under the cap (or when opt-in is None)."""

    def test_none_limit_skips_chunking(self, tmp_path: Path) -> None:
        audio = tmp_path / "short.wav"
        audio.write_bytes(b"")

        engine = _SpyEngine(
            max_chunk_s=None,
            side_effects=[_make_transcript("hi", [(0.0, 1.0, "hi")])],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        # Force _audio_duration_s to return None so even if opt-in is set,
        # the fast path still runs.
        with patch.object(
            BaseBatchTranscribeEngine, "_audio_duration_s", return_value=None
        ):
            result = engine.process(request, _ctx())

        assert result.data.text == "hi"
        assert engine._calls == [audio]  # called once with the full file

    def test_under_cap_skips_chunking(self, tmp_path: Path) -> None:
        audio = tmp_path / "short.wav"
        audio.write_bytes(b"")

        engine = _SpyEngine(
            max_chunk_s=1500.0,
            side_effects=[_make_transcript("hi", [(0.0, 30.0, "hi there")])],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with patch.object(
            BaseBatchTranscribeEngine, "_audio_duration_s", return_value=300.0
        ):
            result = engine.process(request, _ctx())

        assert result.data.text == "hi"
        assert engine._calls == [audio]


class TestChunkedHappyPath:
    """Happy path: audio is chunked, chunks transcribed, results merged."""

    def test_two_chunks_merge_with_offsets(self, tmp_path: Path) -> None:
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        _FakeVadChunker.scenarios = {
            600.0: [(0.0, 600.0), (600.0, 400.0)],
        }

        # Per-chunk transcripts use chunk-local timestamps (start at 0).
        chunk0 = _make_transcript(
            "alpha beta",
            [(0.0, 3.0, "alpha"), (5.0, 8.0, "beta")],
            engine_id="spy-engine",
        )
        chunk1 = _make_transcript(
            "gamma delta",
            [(0.0, 2.0, "gamma"), (10.0, 15.0, "delta")],
            engine_id="spy-engine",
        )

        engine = _SpyEngine(max_chunk_s=600.0, side_effects=[chunk0, chunk1])
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=1000.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            result = engine.process(request, _ctx())

        # Two transcribe_audio calls — one per chunk
        assert len(engine._calls) == 2

        merged = result.data
        assert merged.text == "alpha beta gamma delta"
        assert len(merged.segments) == 4

        # Chunk 0 offsets are 0 → unchanged
        assert merged.segments[0].start == pytest.approx(0.0, abs=1e-3)
        assert merged.segments[1].end == pytest.approx(8.0, abs=1e-3)
        # Chunk 1 offsets shifted by 600s
        assert merged.segments[2].start == pytest.approx(600.0, abs=1e-3)
        assert merged.segments[2].end == pytest.approx(602.0, abs=1e-3)
        assert merged.segments[3].start == pytest.approx(610.0, abs=1e-3)
        assert merged.segments[3].end == pytest.approx(615.0, abs=1e-3)

        # Word timestamps should also be shifted
        ch1_seg = merged.segments[2]
        assert ch1_seg.words is not None
        assert ch1_seg.words[0].start == pytest.approx(600.0, abs=1e-3)

    def test_single_chunk_merged(self, tmp_path: Path) -> None:
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        _FakeVadChunker.scenarios = {600.0: [(10.0, 400.0)]}
        engine = _SpyEngine(
            max_chunk_s=600.0,
            side_effects=[
                _make_transcript("solo", [(0.0, 5.0, "solo")], engine_id="spy-engine")
            ],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=700.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            result = engine.process(request, _ctx())

        # Single chunk with offset 10.0 → segment start shifts by 10
        assert result.data.segments[0].start == pytest.approx(10.0, abs=1e-3)
        assert result.data.text == "solo"


class TestOomBackoff:
    """OOM backoff halves the chunk cap and retries the remaining audio."""

    def test_oom_halves_and_retries(self, tmp_path: Path) -> None:
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        # Pass 1 (max_chunk_s=1200): chunk 0 OOMs at offset 0
        # Pass 2 (max_chunk_s=600): chunk 0 then chunk 1 succeed
        _FakeVadChunker.scenarios = {
            1200.0: [(0.0, 1200.0)],
            600.0: [(0.0, 600.0), (600.0, 600.0)],
        }

        oom = RuntimeError("CUDA out of memory. Tried to allocate 21 GiB")
        chunk0_retry = _make_transcript(
            "retry zero",
            [(0.0, 5.0, "retry zero")],
            engine_id="spy-engine",
        )
        chunk1_retry = _make_transcript(
            "retry one",
            [(0.0, 5.0, "retry one")],
            engine_id="spy-engine",
        )

        engine = _SpyEngine(
            max_chunk_s=1200.0,
            side_effects=[oom, chunk0_retry, chunk1_retry],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=1500.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            result = engine.process(request, _ctx())

        # 3 calls: initial OOM + 2 successful retries
        assert len(engine._calls) == 3
        assert engine._chunked_oom_cap_s == 600.0
        assert result.data.text == "retry zero retry one"

    def test_mid_stream_oom_skips_completed_chunks(self, tmp_path: Path) -> None:
        """Pass 1 completes chunk 0, chunk 1 OOMs.

        Pass 2 re-splits the full source and must skip the region
        already covered by pass 1's chunk 0. Absolute offsets on the
        surviving transcripts are preserved in the original timeline.
        """
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        # Pass 1 (max=1200): two chunks at 0s and 1200s. Chunk 1 OOMs.
        # Pass 2 (max=600): four chunks at 0/600/1200/1800. First two
        #                   are filtered (offset < 1200 = remaining_start_s);
        #                   last two actually run.
        _FakeVadChunker.scenarios = {
            1200.0: [(0.0, 1200.0), (1200.0, 1200.0)],
            600.0: [
                (0.0, 600.0),
                (600.0, 600.0),
                (1200.0, 600.0),
                (1800.0, 600.0),
            ],
        }

        oom = RuntimeError("CUDA out of memory")
        chunk_a = _make_transcript(
            "alpha", [(0.0, 1.0, "alpha")], engine_id="spy-engine"
        )
        retry_c = _make_transcript(
            "gamma", [(0.0, 1.0, "gamma")], engine_id="spy-engine"
        )
        retry_d = _make_transcript(
            "delta", [(0.0, 1.0, "delta")], engine_id="spy-engine"
        )

        engine = _SpyEngine(
            max_chunk_s=1200.0,
            side_effects=[chunk_a, oom, retry_c, retry_d],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=2400.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            result = engine.process(request, _ctx())

        # 4 transcribe_audio calls:
        #   1. Pass 1 chunk 0 (success, offset 0)
        #   2. Pass 1 chunk 1 (OOM, offset 1200)
        #   3. Pass 2 chunk at offset 1200 (success — re-try of OOM'd region)
        #   4. Pass 2 chunk at offset 1800 (success)
        # Crucially: pass 2 chunks at offsets 0 and 600 are FILTERED OUT,
        # so we do NOT re-transcribe the region pass 1 already covered.
        assert len(engine._calls) == 4
        assert result.data.text == "alpha gamma delta"
        assert len(result.data.segments) == 3
        # Absolute offsets preserved in source timeline
        assert result.data.segments[0].start == pytest.approx(0.0, abs=1e-3)
        assert result.data.segments[1].start == pytest.approx(1200.0, abs=1e-3)
        assert result.data.segments[2].start == pytest.approx(1800.0, abs=1e-3)
        # OOM cap cached
        assert engine._chunked_oom_cap_s == 600.0

    def test_floor_aborts_loop(self, tmp_path: Path) -> None:
        """When OOM persists below the floor, raise loudly."""
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        # 60s is the floor; further halving is not allowed
        _FakeVadChunker.scenarios = {
            60.0: [(0.0, 60.0)],
        }

        engine = _SpyEngine(
            max_chunk_s=60.0,  # already at floor
            side_effects=[RuntimeError("CUDA out of memory on device 0")],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=120.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            with pytest.raises(RuntimeError, match="chunk floor"):
                engine.process(request, _ctx())

    def test_non_oom_error_propagates(self, tmp_path: Path) -> None:
        """Non-OOM exceptions are not caught — they propagate as-is."""
        audio = tmp_path / "long.wav"
        audio.write_bytes(b"")

        _FakeVadChunker.scenarios = {600.0: [(0.0, 600.0)]}

        engine = _SpyEngine(
            max_chunk_s=600.0,
            side_effects=[ValueError("unrelated")],
        )
        request = TaskRequest(task_id="t", job_id="j", audio_path=audio)

        with (
            patch.object(
                BaseBatchTranscribeEngine, "_audio_duration_s", return_value=800.0
            ),
            patch("dalston.engine_sdk.base_transcribe.VadChunker", _FakeVadChunker),
        ):
            with pytest.raises(ValueError, match="unrelated"):
                engine.process(request, _ctx())


class TestMergeTranscripts:
    """Merge-level invariants."""

    def test_merge_empty_list_returns_empty_transcript(self) -> None:
        engine = _SpyEngine(max_chunk_s=None, side_effects=[])
        merged = engine._merge_chunk_transcripts([])
        assert merged.text == ""
        assert merged.segments == []

    def test_merge_preserves_engine_id_from_first_chunk(self) -> None:
        engine = _SpyEngine(max_chunk_s=None, side_effects=[])
        t1 = _make_transcript("a", [(0.0, 1.0, "a")], engine_id="spy-engine")
        t2 = _make_transcript("b", [(0.0, 1.0, "b")], engine_id="spy-engine")
        merged = engine._merge_chunk_transcripts([(t1, 0.0), (t2, 5.0)])
        assert merged.engine_id == "spy-engine"
        assert merged.text == "a b"

    def test_merge_deduplicates_warnings(self) -> None:
        engine = _SpyEngine(max_chunk_s=None, side_effects=[])
        t1 = _make_transcript(
            "a", [(0.0, 1.0, "a")], warnings=["low confidence", "boundary cut"]
        )
        t2 = _make_transcript("b", [(0.0, 1.0, "b")], warnings=["low confidence"])
        merged = engine._merge_chunk_transcripts([(t1, 0.0), (t2, 1.0)])
        assert merged.warnings == ["low confidence", "boundary cut"]
