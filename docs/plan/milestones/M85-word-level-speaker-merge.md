# M85: Word-Level Speaker Attribution in Merge

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Assign speakers per word (not per segment) so speaker changes mid-sentence are correctly attributed |
| **Duration**       | 4–6 days                                                     |
| **Dependencies**   | M04 (Speaker Diarization)                                    |
| **Deliverable**    | Word-level speaker merge, sentence-mode smoothing, interval tree lookup, `split_on_speaker_change` implementation |
| **Status**         | Not Started                                                  |

## User Story

> *"As a user transcribing a multi-speaker conversation, I want each word attributed to the correct speaker — even when a speaker change happens mid-sentence — so that the transcript accurately reflects who said what."*

---

## Outcomes

| Scenario | Current | After M85 |
| -------- | ------- | ---------- |
| Speaker change mid-segment: A says "Hello how are" then B says "you doing today" | Entire segment assigned to whichever speaker has more overlap (B wins, "Hello how are" misattributed) | Segment split at the speaker boundary; two segments with correct speakers |
| Background listener says "hmm" during monologue | May flip the entire segment to the listener if "hmm" overlaps more | Mode smoothing assigns the sentence to the majority speaker; listener's "hmm" is absorbed |
| Long segment (30s) spanning 3 speaker turns | Entire segment goes to majority speaker; minority speakers' words are lost | Segment split into 3 sub-segments, each with the correct speaker |
| Diarization boundary jitter (±200ms around actual speaker change) | Whole segment flipped if jitter pushes majority to wrong speaker | Word-level midpoint is resilient to ±200ms jitter; mode smoothing handles the rest |

---

## Motivation

The current merger assigns speakers at the **segment level** using maximum-overlap voting. This works when segments are short and speaker turns align with segment boundaries. It fails when:

1. A speaker change happens mid-sentence (common in natural conversation)
2. A brief interjection ("yeah", "hmm") from a listener occurs during a monologue
3. Segments are long (Whisper can produce 30s segments)

Industry practice (WhisperX, Google Cloud STT, AssemblyAI) assigns speakers **per word**, not per segment. WhisperX uses word-midpoint lookup; Google uses a joint model; whisper-diarization adds mode-smoothing per sentence. All produce significantly better speaker attribution than segment-level overlap.

The `split_on_speaker_change` field already exists in `MergeRequest` (pipeline_types.py:531) but is never read by the merger. The `merge_strategy` field supports "word" mode but only "segment" is implemented. This milestone fills both gaps.

---

## Architecture

### Word-Level Speaker Assignment

```
┌─────────────────────────────────────────────────────────────────┐
│  Merger — word-level speaker attribution                         │
│                                                                  │
│  Input:                                                          │
│    - Transcript with word-level timestamps (from transcribe/align)│
│    - Speaker turns from diarization                              │
│                                                                  │
│  Step 1: Build interval index from speaker turns                 │
│    SpeakerIndex = IntervalTree of (start, end) → speaker         │
│    O(T log T) build, O(log T) per query                         │
│                                                                  │
│  Step 2: Assign speaker per word                                 │
│    For each word:                                                │
│      midpoint = (word.start + word.end) / 2                     │
│      speaker = SpeakerIndex.query(midpoint)                     │
│      If in overlap region: pick speaker with more total overlap  │
│                                                                  │
│  Step 3: Mode smoothing (optional, per sentence)                 │
│    For each sentence (delimited by punctuation):                 │
│      majority_speaker = mode(word.speaker for word in sentence)  │
│      if minority_count / total_count < threshold (e.g. 0.15):   │
│        reassign all words to majority_speaker                   │
│      (Handles diarization jitter and background interjections)   │
│                                                                  │
│  Step 4: Re-segment at speaker boundaries                        │
│    Walk words left to right                                      │
│    When word.speaker != prev_word.speaker → start new segment    │
│    Each segment inherits its speaker from its words              │
│                                                                  │
│  Output: list[MergedSegment] — same schema, more segments,      │
│          each with correct speaker attribution                   │
└─────────────────────────────────────────────────────────────────┘
```

### Overlap Handling

When diarization reports overlapping speakers (two turns covering the same time):

```
Turn A: |████████████████|
Turn B:           |████████████████|
         overlap region ↑

Word in overlap region:
  Option 1: Assign to speaker with more overlap at the word's midpoint
  Option 2: Mark word with overlapping_speakers list (already exists on Word)
```

For the initial implementation, use Option 1 (single speaker per word, pick the one with more overlap). Option 2 is a future enhancement for transcript viewers that want to show overlap visually.

### Data Model Changes

The `Word` type (pipeline_types.py:170) currently has no `speaker` field. Two options:

**Option A** (recommended): Don't add a `speaker` field to `Word`. The merger already produces `MergedSegment` with a `speaker` field and a `words` list. After re-segmenting, each segment's words all belong to the same speaker. The segment is the contract boundary.

**Option B**: Add `speaker: str | None` to `Word`. This is useful for downstream consumers that process word-level data, but it changes the pipeline schema and requires updating all consumers.

Recommendation: **Option A** — no schema change, no downstream impact.

---

## Steps

### 85.1: Interval Tree for Speaker Lookup

**Files modified:**

- `dalston/engine_sdk/speaker_index.py` *(new)*

**Deliverables:**

A lightweight interval tree for O(log N) speaker lookup by timestamp. No external dependency — use `bisect` from stdlib.

```python
class SpeakerIndex:
    """Fast speaker lookup by timestamp using a sorted interval list."""

    def __init__(self, turns: list[SpeakerTurn]) -> None:
        """Build index from diarization turns. O(T log T)."""
        ...

    def speaker_at(self, time: float) -> str | None:
        """Return the speaker active at the given time. O(log T).

        If multiple speakers overlap at this time, returns the one
        whose turn has more remaining duration past this point.
        """
        ...

    def speakers_at(self, time: float) -> list[str]:
        """Return all speakers active at the given time (for overlap detection)."""
        ...
```

---

### 85.2: Word-Level Speaker Assignment

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — new method `_assign_word_speakers()`

**Deliverables:**

Replace the current `_find_speaker_by_overlap()` with a word-level approach:

```python
def _assign_word_speakers(
    self,
    words: list[Word],
    speaker_index: SpeakerIndex,
) -> list[tuple[Word, str | None]]:
    """Assign a speaker to each word using midpoint lookup.

    Returns (word, speaker) pairs. Speaker is None if the word
    falls outside all diarization turns (silence or undetected speech).
    """
    result = []
    for word in words:
        midpoint = (word.start + word.end) / 2
        speaker = speaker_index.speaker_at(midpoint)
        result.append((word, speaker))
    return result
```

---

### 85.3: Mode Smoothing

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — new method `_smooth_speakers()`

**Deliverables:**

Per-sentence mode smoothing to handle diarization jitter and background interjections:

```python
def _smooth_speakers(
    self,
    word_speakers: list[tuple[Word, str | None]],
    threshold: float = 0.15,
) -> list[tuple[Word, str | None]]:
    """Smooth speaker assignments per sentence using majority vote.

    For each sentence (delimited by sentence-ending punctuation),
    if minority speakers account for less than `threshold` of the
    sentence's words, reassign all words to the majority speaker.

    This handles:
    - Diarization boundary jitter (1-2 words at transitions)
    - Background "hmm"/"yeah" during monologues
    """
    ...
```

Sentence detection uses the same `_is_sentence_ending()` logic already in the codebase (checks for `.`, `?`, `!` at word end).

---

### 85.4: Re-Segmentation at Speaker Boundaries

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — modify segment building loop

**Deliverables:**

When `split_on_speaker_change=True` (from `MergeRequest`), split segments at speaker boundaries:

```python
def _resegment_by_speaker(
    self,
    word_speakers: list[tuple[Word, str | None]],
    original_segment: TranscriptSegment,
) -> list[MergedSegment]:
    """Split a transcript segment into sub-segments at speaker changes.

    Walks through words left to right. When the speaker changes,
    starts a new MergedSegment. Each sub-segment inherits metadata
    from the original segment (language, temperature, etc.).
    """
    ...
```

The main segment-building loop changes from:

```python
# Current: one segment → one speaker
speaker = self._find_speaker_by_overlap(seg_start, seg_end, turns)
merged_segments.append(MergedSegment(speaker=speaker, ...))
```

To:

```python
# New: one segment → possibly multiple sub-segments
if split_on_speaker_change and words:
    word_speakers = self._assign_word_speakers(words, speaker_index)
    word_speakers = self._smooth_speakers(word_speakers)
    sub_segments = self._resegment_by_speaker(word_speakers, segment)
    merged_segments.extend(sub_segments)
else:
    # Existing path: segment-level overlap (backward compatible)
    speaker = self._find_speaker_by_overlap(seg_start, seg_end, turns)
    merged_segments.append(MergedSegment(speaker=speaker, ...))
```

---

### 85.5: Wire `split_on_speaker_change` Config

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — read the existing config field

**Deliverables:**

Read `split_on_speaker_change` from `MergeRequest` (already defined in pipeline_types.py:531) and pass it through to the segment-building logic. Default is `False` — existing behavior unchanged unless explicitly enabled.

The orchestrator can set this per-job via the pipeline config. API callers can request it via the transcription options.

---

## Non-Goals

- **Adding `speaker` field to `Word` type** — keep the segment as the speaker-attribution boundary. Re-evaluate if downstream consumers need per-word speakers.
- **Multi-speaker word attribution** — a word in an overlap region gets one speaker, not multiple. The `overlapping_speakers` field on `Word` is reserved for future use.
- **Joint ASR + diarization model** — that's a different architecture (Google's approach). We stay with the modular pipeline.
- **Real-time merge** — this milestone covers batch merge only. Real-time merge is a separate concern.

---

## Verification

```bash
make dev

# 1. Transcribe a multi-speaker file with word timestamps + diarization
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/two_speakers.wav" \
  -F "diarize=true" \
  -F "split_on_speaker_change=true" | jq '.segments[:5]'

# Expect: segments split at speaker boundaries, each with correct speaker

# 2. Compare with current behavior (split_on_speaker_change=false)
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/two_speakers.wav" \
  -F "diarize=true" | jq '.segments[:5]'

# Expect: same as today — whole segments, majority-speaker assignment

# 3. Verify mode smoothing handles background interjections
# Use a file where one speaker says "hmm" during the other's monologue
# The "hmm" should not cause a segment split
```

---

## Checkpoint

- [ ] `SpeakerIndex` builds from `list[SpeakerTurn]` and provides O(log T) lookup
- [ ] `SpeakerIndex` handles overlapping turns (returns speaker with more overlap)
- [ ] Word-level speaker assignment produces correct results on a 2-speaker test file
- [ ] Mode smoothing prevents segment splits for minority-speaker interjections (<15% of sentence)
- [ ] Re-segmentation splits segments at speaker boundaries when `split_on_speaker_change=True`
- [ ] Default behavior (`split_on_speaker_change=False`) is unchanged — no regression
- [ ] Performance: word-level merge adds <100ms for a 1-hour file (interval tree, not O(W×T))

---

## References

- [WhisperX — `assign_word_speakers`](https://github.com/m-bain/whisperX) — word midpoint assignment
- [WhisperX interval tree speedup (228x)](https://github.com/m-bain/whisperX/issues/1335)
- [whisper-diarization — mode-based sentence realignment](https://github.com/MahmoudAshraf97/whisper-diarization)
- [NeMo — ASR-based VAD and overlap-aware MSDD](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/models.html)
- [Google Cloud STT — per-word speaker_tag](https://docs.google.com/speech-to-text/docs/multiple-voices)
