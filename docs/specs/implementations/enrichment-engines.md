# Enrichment Engine Patterns

## Emotion Detection (emotion2vec)

Uses FunASR's emotion2vec model for utterance-level emotion classification.

```python
from funasr import AutoModel

class Emotion2VecEngine(Engine):
    def __init__(self):
        self.model = None

    def _load_model(self):
        if self.model is None:
            self.model = AutoModel(
                model="iic/emotion2vec_plus_large",
                device="cuda"
            )

    def process(self, input: TaskInput) -> TaskOutput:
        self._load_model()

        audio_path = input.previous_outputs["prepare"]["audio_path"]
        segments = input.previous_outputs.get("align", input.previous_outputs.get("transcribe"))["segments"]

        results = []
        for seg in segments:
            seg_audio = self._extract_segment(audio_path, seg["start"], seg["end"])

            # Skip very short segments
            if len(seg_audio) < 1600:  # ~100ms at 16kHz
                results.append(self._neutral_result(seg))
                continue

            rec = self.model.generate(seg_audio, granularity="utterance")

            # emotion2vec labels: angry, disgusted, fearful, happy, neutral, sad, surprised
            emotion_labels = ["angry", "disgusted", "fearful", "happy", "neutral", "sad", "surprised"]
            scores = rec[0]["scores"]

            best_idx = scores.index(max(scores))
            emotion = emotion_labels[best_idx]

            # Simplify to positive/negative/neutral
            simplified = self._simplify_emotion(emotion)

            results.append({
                "segment_id": seg.get("id"),
                "start": seg["start"],
                "end": seg["end"],
                "emotion": simplified,
                "emotion_detailed": emotion,
                "confidence": scores[best_idx],
                "all_scores": dict(zip(emotion_labels, scores))
            })

        return TaskOutput(data={"emotions": results})

    def _simplify_emotion(self, emotion: str) -> str:
        return {
            "angry": "negative",
            "disgusted": "negative",
            "fearful": "negative",
            "sad": "negative",
            "happy": "positive",
            "surprised": "positive",
            "neutral": "neutral"
        }.get(emotion, "neutral")
```

---

## Audio Events (PANNs)

Uses PANNs for audio event detection with sliding windows.

```python
class PANNsEventsEngine(Engine):
    # Events we care about (subset of AudioSet)
    RELEVANT_EVENTS = {
        "Laughter": "laughter",
        "Applause": "applause",
        "Music": "music",
        "Cough": "cough",
        "Sigh": "sigh",
        "Crying, sobbing": "crying",
        "Cheering": "cheering",
        "Clapping": "clapping",
        "Crowd": "crowd",
        "Silence": "silence"
    }

    WINDOW_SIZE = 2.0   # seconds
    HOP_SIZE = 1.0      # seconds
    THRESHOLD = 0.3     # confidence threshold

    def process(self, input: TaskInput) -> TaskOutput:
        if self.model is None:
            from panns_inference import AudioTagging
            self.model = AudioTagging(checkpoint_path=None, device='cuda')

        audio_path = input.previous_outputs["prepare"]["audio_path"]
        audio, sr = sf.read(audio_path)

        events = []
        for start in np.arange(0, len(audio) / sr - self.WINDOW_SIZE, self.HOP_SIZE):
            end = start + self.WINDOW_SIZE
            window = audio[int(start * sr):int(end * sr)]

            clipwise_output, _ = self.model.inference(window[None, :])

            # Check top predictions against relevant events
            top_indices = np.argsort(clipwise_output[0])[-10:][::-1]
            for idx in top_indices:
                label = self.model.labels[idx]
                prob = clipwise_output[0][idx]

                if label in self.RELEVANT_EVENTS and prob > self.THRESHOLD:
                    events.append({
                        "type": self.RELEVANT_EVENTS[label],
                        "start": start,
                        "end": end,
                        "confidence": float(prob)
                    })

        # Merge adjacent events of same type
        merged = self._merge_adjacent_events(events)
        return TaskOutput(data={"events": merged})

    def _merge_adjacent_events(self, events: list) -> list:
        """Merge events of same type within 0.5s of each other."""
        if not events:
            return []

        events = sorted(events, key=lambda e: (e["type"], e["start"]))
        merged = []
        current = None

        for event in events:
            if current is None:
                current = event.copy()
            elif event["type"] == current["type"] and event["start"] <= current["end"] + 0.5:
                # Extend current event
                current["end"] = max(current["end"], event["end"])
                current["confidence"] = max(current["confidence"], event["confidence"])
            else:
                merged.append(current)
                current = event.copy()

        if current:
            merged.append(current)
        return merged
```

---

## LLM Cleanup

Uses Claude API for transcript refinement tasks.

### Error Correction Pattern

```python
def fix_errors(self, segments: list) -> list:
    """Fix transcription errors in batches."""
    BATCH_SIZE = 15

    for i in range(0, len(segments), BATCH_SIZE):
        batch = segments[i:i + BATCH_SIZE]
        batch_text = "\n".join(
            f"[{s['start']:.2f}s] {s['text']}"
            for s in batch
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""Fix obvious transcription errors in this text.
Common errors: homophones, proper nouns, technical terms, run-on sentences.
Preserve timestamps exactly. Only fix clear errors, don't rephrase.

{batch_text}

Return ONLY the corrected text, one line per segment, same format [timestamp] text."""
            }]
        )

        # Parse and apply corrections
        corrected_lines = response.content[0].text.strip().split("\n")
        for j, line in enumerate(corrected_lines):
            if i + j < len(segments):
                match = re.match(r'\[[\d.]+s\]\s*(.+)', line)
                if match:
                    segments[i + j]["text"] = match.group(1).strip()

    return segments
```

### Speaker Identification Pattern

```python
def identify_speakers(self, segments: list, speakers: list) -> list:
    """Identify speaker names from transcript context."""

    # Gather sample utterances per speaker
    speaker_samples = {}
    for seg in segments:
        spk = seg.get("speaker")
        if spk and spk not in speaker_samples:
            speaker_samples[spk] = []
        if spk and len(speaker_samples[spk]) < 5:
            speaker_samples[spk].append(seg["text"])

    if not speaker_samples:
        return speakers

    samples_text = "\n\n".join(
        f"{spk}:\n" + "\n".join(f'  "{t}"' for t in texts)
        for spk, texts in speaker_samples.items()
    )

    response = self.client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Based on this transcript, identify the speakers.
Look for: introductions, name mentions, role references, speaking style.

{samples_text}

Return JSON only: {{"SPEAKER_00": "Name or Role", "SPEAKER_01": "Name or Role"}}
If unknown, use descriptive labels like "Host", "Guest 1", "Interviewer"."""
        }]
    )

    # Parse JSON from response and apply labels
    try:
        text = response.content[0].text
        json_match = re.search(r'\{[^}]+\}', text)
        if json_match:
            labels = json.loads(json_match.group())
            for speaker in speakers:
                if speaker["id"] in labels:
                    speaker["label"] = labels[speaker["id"]]
    except Exception:
        pass  # Keep original speaker IDs on parse failure

    return speakers
```

---

## Merger Integration

The merger collects enrichment outputs and integrates them:

```python
def process(self, input: TaskInput) -> TaskOutput:
    # Get base segments from align or transcribe
    segments = input.previous_outputs.get("align", input.previous_outputs.get("transcribe"))["segments"]
    speakers = input.previous_outputs.get("diarize", {}).get("speakers", [])

    # Add emotions to segments
    emotions = input.previous_outputs.get("detect_emotions", {}).get("emotions", [])
    emotion_map = {e["segment_id"]: e for e in emotions}
    for seg in segments:
        if seg["id"] in emotion_map:
            seg["emotion"] = emotion_map[seg["id"]]["emotion"]
            seg["emotion_confidence"] = emotion_map[seg["id"]]["confidence"]

    # Get audio events
    events = input.previous_outputs.get("detect_events", {}).get("events", [])

    # Apply LLM refinements
    refine = input.previous_outputs.get("refine", {})
    if refine:
        if refine.get("segments"):
            segments = refine["segments"]
        if refine.get("speakers"):
            speakers = refine["speakers"]

    return TaskOutput(data={
        "text": " ".join(s["text"] for s in segments),
        "segments": segments,
        "speakers": speakers,
        "events": events,
        "summary": refine.get("summary"),
        "metadata": {
            "enrichment": {
                "emotions": len(emotions) > 0,
                "events": len(events) > 0,
                "llm_cleanup": bool(refine)
            }
        }
    })
```
