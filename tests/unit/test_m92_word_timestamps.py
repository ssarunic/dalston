"""Unit tests for M92 step 92.6: word-timestamp integrity.

The strict bracket filter in NeMo hypothesis parsing dropped every word
when NeMo's segment spans disagreed with word timings. Words are now
assigned by midpoint containment with nearest-segment fallback, and a
words-expected engine warns instead of silently downgrading granularity.
"""

from types import SimpleNamespace

from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.inference.nemo_inference import NemoInference


def _hypothesis(words: list[dict], segments: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        text=" ".join(w["word"] for w in words),
        timestamp={"word": words, "segment": segments},
    )


class TestMidpointWordAssignment:
    def test_words_inside_segment_are_assigned(self):
        hyp = _hypothesis(
            words=[
                {"word": "hello", "start": 0.3, "end": 0.8},
                {"word": "world", "start": 0.9, "end": 1.4},
            ],
            segments=[{"start": 0.24, "end": 25.92, "segment": "hello world"}],
        )
        segments, all_words = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert len(all_words) == 2
        assert len(segments) == 1
        assert [w.word for w in segments[0].words] == ["hello", "world"]

    def test_word_outside_all_segments_attaches_to_nearest(self):
        # Word end exceeds the segment end — the old bracket filter
        # (w.end <= seg_end + 0.01) dropped it entirely.
        hyp = _hypothesis(
            words=[
                {"word": "early", "start": 0.0, "end": 0.5},
                {"word": "late", "start": 2.5, "end": 3.2},
            ],
            segments=[{"start": 0.0, "end": 2.0, "segment": "early late"}],
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert [w.word for w in segments[0].words] == ["early", "late"]

    def test_ties_go_to_earlier_segment(self):
        # Midpoint 1.0 is contained by both spans; first containing wins.
        hyp = _hypothesis(
            words=[{"word": "shared", "start": 0.8, "end": 1.2}],
            segments=[
                {"start": 0.0, "end": 1.0, "segment": "a"},
                {"start": 1.0, "end": 2.0, "segment": "b"},
            ],
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert [w.word for w in segments[0].words] == ["shared"]
        assert segments[1].words == []

    def test_bounds_expand_to_cover_attached_words(self):
        # R5: a word attached via nearest-segment fallback that precedes the
        # segment must expand the start — no word may lie outside its
        # parent segment's bounds, and no zero-duration inversion.
        hyp = _hypothesis(
            words=[{"word": "early", "start": 0.0, "end": 0.4}],
            segments=[{"start": 2.0, "end": 5.0, "segment": "early"}],
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        seg = segments[0]
        assert seg.words[0].word == "early"
        assert seg.start <= seg.words[0].start
        assert seg.end >= seg.words[0].end
        assert seg.end >= seg.start
        assert seg.start == 0.0
        assert seg.end == 0.4  # clamped to recognized content

    def test_words_split_across_segments_by_midpoint(self):
        hyp = _hypothesis(
            words=[
                {"word": "one", "start": 0.0, "end": 0.4},
                {"word": "two", "start": 3.0, "end": 3.4},
            ],
            segments=[
                {"start": 0.0, "end": 1.0, "segment": "one"},
                {"start": 2.9, "end": 4.0, "segment": "two"},
            ],
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert [w.word for w in segments[0].words] == ["one"]
        assert [w.word for w in segments[1].words] == ["two"]


class TestGranularityDowngradeWarning:
    def _segments(self, with_words: bool):
        seg_words = None
        if with_words:
            seg_words = [
                BaseBatchTranscribeEngine.build_word(text="hi", start=0.0, end=0.5)
            ]
        return [
            BaseBatchTranscribeEngine.build_segment(
                start=0.0, end=1.0, text="hi", words=seg_words
            )
        ]

    def test_warns_when_words_expected_but_absent(self):
        t = BaseBatchTranscribeEngine.build_transcript(
            text="hi",
            segments=self._segments(with_words=False),
            language="en",
            engine_id="nemo",
            words_expected=True,
        )
        assert any("Word timestamps were expected" in w for w in t.warnings)
        assert t.timestamp_granularity.value == "segment"

    def test_no_warning_when_words_present(self):
        t = BaseBatchTranscribeEngine.build_transcript(
            text="hi",
            segments=self._segments(with_words=True),
            language="en",
            engine_id="nemo",
            words_expected=True,
        )
        assert t.warnings == []
        assert t.timestamp_granularity.value == "word"

    def test_no_warning_when_words_not_expected(self):
        t = BaseBatchTranscribeEngine.build_transcript(
            text="hi",
            segments=self._segments(with_words=False),
            language="en",
            engine_id="faster-whisper",
        )
        assert t.warnings == []

    def test_no_warning_on_empty_transcript(self):
        t = BaseBatchTranscribeEngine.build_transcript(
            text="",
            segments=[],
            language="en",
            engine_id="nemo",
            words_expected=True,
        )
        assert t.warnings == []
