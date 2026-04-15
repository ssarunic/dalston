# M91: Cleaned-Output Mode (`no_verbatim`)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | A single request flag that returns a filler-free, readable transcript — matching ElevenLabs' `no_verbatim` mode with a rule-based cleaner for the fast path and an optional LLM refine stage for high-quality output |
| **Duration**       | 4–6 days                                                     |
| **Dependencies**   | None for the rule-based path. Optional LLM refine stage depends on `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` at runtime. |
| **Deliverable**    | `cleaned_output` request field (alias `no_verbatim` on ElevenLabs route), rule-based post-processor, optional LLM refine engine, response model updates |
| **Status**         | Not Started                                                  |

## User Story

> *"As someone generating meeting notes, I want a transcript that reads cleanly — no 'um', no 'uh', no stuttering, no 'so, like, you know, the thing' — without post-processing it myself. I pass `cleaned_output=true`, I get back a readable transcript. If I want to keep the raw version, I get both."*

---

## Outcomes

| Scenario | Current | After M91 |
| -------- | ------- | --------- |
| Meeting recording with lots of fillers → want readable minutes | Caller receives raw transcript with every "um" and "uh", has to post-process | `cleaned_output=true` returns filler-free text; raw available via `include_raw=true` |
| ElevenLabs user migrates, passes `no_verbatim=true` (their flag name) | Field ignored, raw transcript returned | Alias recognized on ElevenLabs-compat route, returns cleaned text |
| Subtitle generation for a podcast | Hand-edits needed to remove stutters | Cleaned segments are shorter and timestamps are preserved on word boundaries |
| Legal/medical use case that needs every word | No option needed (default is raw) | No change — default stays verbatim, cleanup is opt-in |

---

## Motivation

ElevenLabs shipped `no_verbatim` in their Jan 2026 changelog and it's been consistently praised in 2026 comparison posts. It removes fillers (`um`, `uh`, `er`), collapses repeated phrases (`the the the`), and smooths stutters (`I-I-I think`). The feature exists because the majority of STT consumers want readable text, not a faithful audio log.

Dalston has no equivalent today. The CLAUDE.md file references an `engines/stt-cleanup/llm-cleanup` engine but no such engine exists in the repo (verified against `engines/` tree at 2026-04-15). So this milestone builds it, with two paths:

1. **Rule-based cleaner.** Fast, deterministic, runs on every request. Handles the 80% case (single-word fillers, repeated starts, stutter patterns). Zero external dependencies, no network calls, no API cost.
2. **Optional LLM refine stage.** Opt-in, for callers that want polished prose. Uses an existing LLM provider via the already-configured `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. Slower, costs money, not on by default.

Callers pick which: `cleaned_output=true` runs rules only. `cleaned_output=llm` runs both. The default stays raw so no existing behavior changes.

---

## Motivation sidebar: why build the rule-based path at all

A naive implementation might skip straight to the LLM refine — after all, modern LLMs clean text beautifully. Three reasons to build the rules first:

- **No tenant should be forced to send transcripts to a third party** to get the "readable" version. Rule-based runs fully on-prem.
- **Deterministic output** matters for regression testing, diff-based QA, and compliance-logged workflows.
- **LLM refine costs $3–15 per million tokens.** For a high-volume tenant that's non-trivial ongoing spend. Rules are free.

The LLM stage is the premium option, not the default.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                      MERGE stage (final-merger)                     │
│                                                                     │
│   transcripts + diarize + pii ──▶ unified Transcript                │
│                                    │                                │
│                                    ▼                                │
│                          cleaned_output = ?                         │
│                                    │                                │
│               ┌────────────────────┼────────────────────┐           │
│               │                    │                    │           │
│               ▼                    ▼                    ▼           │
│            false                "rules"                "llm"        │
│               │                    │                    │           │
│               │                    ▼                    ▼           │
│               │         RuleCleaner.apply(tr)   LlmRefine (new)      │
│               │                    │                    │           │
│               │                    │          ┌─────────┘           │
│               │                    │          ▼                     │
│               │                    │     RuleCleaner.apply(tr)       │
│               │                    │      (always run first)         │
│               │                    │          │                     │
│               │                    │          ▼                     │
│               │                    │     LLM refine (Anthropic/OpenAI)│
│               │                    │          │                     │
│               └────────────────────┴──────────┘                     │
│                                    │                                │
│                                    ▼                                │
│                    Response: {text, segments, raw?: Transcript}     │
└────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 91.1: `RuleCleaner` utility

**Files modified:**

- `dalston/common/text_cleanup.py` *(new)* — deterministic rule-based cleaner
- `tests/unit/test_rule_cleaner.py` *(new)*

**Deliverables:**

A pure-Python utility that takes a `Transcript` (segments + words) and returns a cleaned copy with:

- **Standalone fillers removed:** `um`, `uh`, `er`, `erm`, `uhh`, `ahh`, `hmm`, `mm-hmm`. Locale-aware; the default word list is English-only, loadable per-language via `RULE_FILLERS_BY_LANG`.
- **Repeated consecutive words collapsed:** `the the the cat` → `the cat`. Limited to single-word repetitions of 2–4 reps to avoid false positives on legitimate repetition (e.g., "very very fast").
- **Stutter patterns collapsed:** `I-I-I think` → `I think`. Detect hyphenated or letter-initial stutter via a regex on the surface word, not the audio.
- **False starts removed:** a segment that ends with a hanging fragment (2–4 words) followed by a comma or pause before a fresh sentence start → drop the fragment. Conservative heuristic; opt-in via `strict_cleanup=true`.
- **Whitespace normalization** and sentence-boundary capitalization.

```python
# dalston/common/text_cleanup.py

@dataclass
class CleanupStats:
    fillers_removed: int
    repeats_collapsed: int
    stutters_collapsed: int
    words_before: int
    words_after: int

class RuleCleaner:
    def __init__(self, language: str = "en", strict: bool = False) -> None: ...

    def apply(self, transcript: Transcript) -> tuple[Transcript, CleanupStats]:
        """Return a cleaned copy + stats. Preserves word timestamps for
        surviving words; removed words simply drop out of the word list
        and the segment's text is re-rendered from the surviving words."""
        ...
```

**Timestamp handling:** surviving words keep their original `start`/`end`. The segment's `text` is re-rendered from `" ".join(w.text for w in kept_words)`. This guarantees `segments[*].text` aligns with `segments[*].words[*].text` in the cleaned output.

**Tests:**

- `test_single_fillers_removed` — "um I uh think so" → "I think so"
- `test_repeats_collapsed` — "the the the cat" → "the cat"; "very very fast" → "very very fast" (not collapsed)
- `test_stutter_collapsed` — "I-I-I think" → "I think"; "Mi-Mi-Mississippi" → "Mississippi"
- `test_word_timestamps_preserved` — surviving words keep original `start`/`end`
- `test_segment_text_matches_words` — invariant check
- `test_language_gating` — French transcript with `um` is NOT cleaned when language=fr (French word list doesn't contain `um`)
- `test_strict_mode_removes_false_starts`
- `test_cleanup_stats_reported`

---

### 91.2: `cleaned_output` request field on both routes

**Files modified:**

- `dalston/gateway/models/requests.py` — add `cleaned_output`
- `dalston/gateway/api/v1/transcription.py` — forward to orchestrator job
- `dalston/gateway/api/v1/speech_to_text.py` — ElevenLabs alias
- `tests/unit/test_request_validation.py`

**Deliverables:**

```python
class TranscribeRequest(BaseModel):
    cleaned_output: Literal["false", "rules", "llm"] | bool = Field(
        default=False,
        description=(
            "Return a cleaned transcript with fillers/stutters/repeats removed. "
            "'rules' (or true) uses the deterministic rule cleaner. 'llm' also runs "
            "an LLM refine pass for polished prose. Default false preserves raw."
        ),
    )
    include_raw: bool = Field(
        default=False,
        description="When cleaned_output is set, also include the raw verbatim transcript in the response.",
    )
```

**ElevenLabs alias:** on `/v1/speech-to-text`, accept `no_verbatim=true` and map to `cleaned_output="rules"`. ElevenLabs doesn't expose an LLM-refine option, so `no_verbatim=true` stays in the free rule-based path. The Dalston-native `cleaned_output="llm"` is a Dalston extension.

**Coercion:**

- `true` → `"rules"`
- `false` → `"false"`
- `"rules"` / `"llm"` → as-is
- anything else → 422

---

### 91.3: Merger applies rule cleanup

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — call `RuleCleaner` when `job.cleaned_output in {"rules", "llm"}`
- `dalston/common/types.py` — `Transcript.raw: Transcript | None` field

**Deliverables:**

The final merger stage runs the cleaner on its unified transcript. When `include_raw=true` is also set, it attaches the original verbatim `Transcript` as `cleaned.raw` so clients can access both.

```python
# In final-merger engine
if job.cleaned_output in {"rules", "llm"}:
    raw = transcript
    cleaned, stats = RuleCleaner(language=transcript.language).apply(transcript)
    transcript = cleaned
    transcript.cleanup_stats = stats
    if job.include_raw:
        transcript.raw = raw
```

The LLM refine branch (`cleaned_output="llm"`) is wired in 91.5; for this step, `"llm"` falls through to just rule cleanup with a `warnings` entry noting LLM refine is not yet enabled.

---

### 91.4: Response schema surface

**Files modified:**

- `dalston/schemas/transcript.py` — add `cleanup_stats`, `raw`
- `dalston/gateway/api/v1/export.py` — exports honor cleaned vs raw choice

**Deliverables:**

The JSON response carries:

```json
{
  "transcript_id": "tr_abc",
  "text": "Cleaned transcript text.",
  "segments": [...],
  "cleanup_stats": {
    "fillers_removed": 17,
    "repeats_collapsed": 3,
    "stutters_collapsed": 1,
    "words_before": 428,
    "words_after": 407
  },
  "raw": {
    "text": "Um, so, the, the cleaned transcript, uh, text.",
    "segments": [...]
  }
}
```

`raw` is only populated when `include_raw=true`. `cleanup_stats` is always present when `cleaned_output != "false"`.

**Exports:** SRT / VTT exports default to the cleaned segments when `cleaned_output` is set. Caller can request raw via `?raw=true` on the export endpoint.

---

### 91.5: Optional LLM refine engine

**Files modified:**

- `engines/stt-refine/llm-refine/` *(new)* — LLM-backed refine engine (Dockerfile, requirements, engine.yaml, engine.py)
- `docs/specs/batch/ENGINES.md` — document the new REFINE stage
- `dalston/orchestrator/dag.py` — optional REFINE node after MERGE when `cleaned_output=="llm"`

**Deliverables:**

A new batch engine in a new stage (`stt-refine`). It takes the rule-cleaned transcript and sends it to an LLM with a focused prompt: "Smooth this transcript into natural prose. Preserve every meaningful word. Keep the speaker attributions and the segment boundaries. Do NOT rephrase, summarize, or add information." Returns a refined transcript.

**Model choice:** Configurable via env. Default `claude-haiku-4-5-20251001` for cost/speed balance. Override with `DALSTON_REFINE_MODEL=gpt-4o-mini` etc.

**Prompt caching:** the refine prompt is the same for every request, so aggressively cache it. Rolls into Anthropic prompt caching headers for Claude models.

**Safety rails:**

- **Never send PII to the LLM.** If `redact_pii=true` on the job, the refine engine operates on the already-redacted transcript. This is enforced in the DAG builder — REFINE always comes after PII_DETECT + AUDIO_REDACT.
- **Never run refine by default.** Must be explicit per-request.
- **Character budget:** truncate any single LLM call at 50k characters; chunk long transcripts into non-overlapping segments, refine independently, concatenate.
- **Hallucination guard:** diff the refined text against the raw word list. If the refined output contains more than 5% new tokens that weren't in the raw, reject the refine and fall back to rule-cleaned output with a `warnings` entry.
- **Timeout:** 15 s per chunk. On timeout, fall back to rule-cleaned output.

**Why a separate stage instead of a library:** keeping refine in its own engine means it inherits everything else the engine SDK gives you: isolation, retries, per-stage observability, the option to disable it at deploy time by not running the container. It also lets the REFINE stage be skipped entirely on requests that don't need it, which matters because it's the slowest optional stage in the pipeline.

**Not in scope for M91:**

- Streaming refine (only runs on batch)
- Fine-tuned local models (default is hosted LLM; self-hosted refine tracked separately)

---

## Non-Goals

- **Summarization** — `no_verbatim` is *not* summarization. Segments, words, timestamps all stay. "Cleaned" ≠ "summarized".
- **Paraphrasing** — The LLM refine preserves words. No rephrasing, no reordering.
- **Default-on cleanup** — Default stays raw. Cleanup is always opt-in to avoid silent behavior changes for existing callers.
- **Realtime cleanup** — Batch only. Cleaning partial realtime transcripts live is a different problem (causality, re-emission of already-delivered partials). Track separately.
- **Language support beyond English for the LLM refine path** — Rule cleaner is per-language via the word list; LLM refine works in any language the chosen model supports, but we do not ship tested prompts for every locale. Explicitly document English as the supported language for refine in 91.5.
- **Custom user rules** — No per-tenant filler lists in M91. The default list is a good 80% answer; custom rules are follow-up work if demand shows up.

---

## Deployment

Rolling deploy. `cleaned_output` defaults to `false` so existing clients are unaffected.

The LLM refine engine (91.5) is an **optional** deployment. `docker compose up` without `llm-refine` in the profile means `cleaned_output="llm"` falls back to `"rules"` + a warnings entry. Shipped `docker-compose.yml` includes it under the `refine` profile, off by default.

**API keys:** `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the environment of the refine engine container. Explicit failure (engine won't start) if neither is present and the engine is enabled.

---

## Verification

```bash
make dev

# 1. Rule-based cleanup on a filler-heavy sample
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/meeting-with-fillers.wav" \
  -F "cleaned_output=rules" \
  -F "include_raw=true" \
  | jq '{text, cleanup_stats, raw: .raw.text}'

# Expected:
#   cleanup_stats.fillers_removed > 0
#   text contains no "um" or "uh"
#   raw.text contains original fillers

# 2. ElevenLabs no_verbatim alias
curl -X POST http://localhost:8000/v1/speech-to-text \
  -H "xi-api-key: $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/meeting-with-fillers.wav" \
  -F "no_verbatim=true" \
  | jq '.text'
# Expected: filler-free text

# 3. Default (raw) unchanged
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/meeting-with-fillers.wav" \
  | jq '.text'
# Expected: raw text with fillers intact

# 4. LLM refine (requires refine profile)
make dev-with-refine
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/meeting-with-fillers.wav" \
  -F "cleaned_output=llm" \
  | jq '{text, cleanup_stats}'

# 5. Timestamp preservation invariant
python -m pytest tests/unit/test_rule_cleaner.py::test_word_timestamps_preserved -v
```

---

## Checkpoint

- [ ] **91.1** `RuleCleaner` removes standalone fillers, repeats, stutters, false starts (strict)
- [ ] **91.1** Rule cleaner preserves word-level timestamps
- [ ] **91.1** Language-gated filler word lists
- [ ] **91.2** `cleaned_output` field on Dalston-native route
- [ ] **91.2** `no_verbatim` alias on ElevenLabs-compat route
- [ ] **91.2** `include_raw` flag returns both versions
- [ ] **91.3** Merger applies rule cleanup when flag set
- [ ] **91.4** Response schema carries `cleanup_stats` and optional `raw`
- [ ] **91.4** SRT/VTT/JSON exports honor cleaned vs raw
- [ ] **91.5** `stt-refine/llm-refine` engine ships with Claude + OpenAI backends
- [ ] **91.5** LLM refine runs **after** PII detection and redaction
- [ ] **91.5** Hallucination guard: >5% new tokens → fall back to rules
- [ ] **91.5** LLM refine is a disabled-by-default compose profile
- [ ] Default (no flag) behavior unchanged — regression suite green
