# Task Output Views

## Overview

Stage-specific output visualizations for the Task Detail page in the web console. Instead of showing only raw JSON, each pipeline stage renders a summary view highlighting the most useful information for that stage type.

## Motivation

Raw JSON output is difficult to scan quickly. Different stages produce fundamentally different data - audio metadata vs transcription text vs speaker segments - and each benefits from tailored presentation. This makes debugging faster and the console more useful for operators.

## Stage Views

### Prepare Stage

Displays audio metadata extracted during preparation:

| Field | Source | Format |
|-------|--------|--------|
| Duration | `data.duration` | Seconds with 1 decimal (e.g., "5.6s") |
| Channels | `data.channels` | Integer |
| Sample Rate | `data.sample_rate` | Converted to kHz (e.g., "16kHz") |

### Transcribe Stage

Displays transcription results with text preview:

| Field | Source | Format |
|-------|--------|--------|
| Language | `data.language` | Uppercase code (e.g., "EN") |
| Language Confidence | `data.language_confidence` | Percentage |
| Segment Count | `data.segments.length` | Integer |
| Full Text | `data.text` | Scrollable text box (max 200px) |
| Segments Preview | `data.segments[0:10]` | Timestamp + text per segment |

### Align Stage

Displays word-level alignment status:

| Field | Source | Format |
|-------|--------|--------|
| Language | `data.language` | Uppercase code |
| Word Timestamps | `data.word_timestamps` | "Yes" / "No" |
| Words Aligned | Sum of `segments[].words.length` | Integer with segment count subtext |
| Warning | `data.warning.reason` | Yellow banner if alignment failed |

### Diarize Stage

Displays speaker detection results:

| Field | Source | Format |
|-------|--------|--------|
| Speakers Detected | `data.speakers[]` | Badges for each speaker ID |
| Segment Count | `data.diarization_segments.length` | "N speaker segments detected" |

Handles both string arrays (`["SPEAKER_00"]`) and object arrays (`[{id, label}]`) for speaker format.

### Merge Stage

Displays final transcript summary - the most information-rich view:

| Field | Source | Format |
|-------|--------|--------|
| Language | `metadata.language` | Uppercase with confidence % |
| Duration | `metadata.audio_duration` | Minutes and seconds |
| Segments | `segments.length` | Count with total character count |
| Speakers | `speakers.length` | Count with detection mode subtext |
| Pipeline | `metadata.pipeline_stages` | Stage badges |
| Word Timestamps | `metadata.word_timestamps` | Green badge if available |
| Warnings | `metadata.pipeline_warnings[]` | Yellow banners |
| Transcript Preview | `text` | First 500 characters |

## Data Structure Handling

All views handle two possible data locations:

- Direct: `output.field`
- Nested: `output.data.field`

This accommodates variation in how engines return TaskOutput:

```typescript
const data = (output.data as Record<string, unknown>) ?? output
```

## Raw JSON Toggle

All stage views include a "Show Raw JSON" toggle below the summary view, preserving access to the complete output for advanced debugging.

## Files Modified

| File | Changes |
|------|---------|
| `web/src/pages/TaskDetail.tsx` | Added `PrepareOutputView`, `TranscribeOutputView`, `AlignOutputView`, `DiarizeOutputView`, `MergeOutputView` components |

## Future Stages

When new stages are added (e.g., `pii_detect` for PII detection, `refine` for LLM cleanup), corresponding output views should be added following this pattern:

1. Check engine output format in `engines/{stage}/*/engine.py`
2. Identify 3-5 most useful fields for quick inspection
3. Add `{Stage}OutputView` component
4. Add case to `OutputViewer` switch statement
