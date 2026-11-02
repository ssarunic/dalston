# fluidaudio-server

Loopback HTTP sidecar that runs [FluidAudio](https://github.com/FluidInference/FluidAudio)'s
Core ML models on the Apple Neural Engine for the Dalston Mac worker (M104):

- Parakeet TDT 0.6B v3 and Parakeet Ultra (transcription, word timings)
- pyannote community-1 + VBx (speaker diarization)

The Dalston Python adapters in `engines/stt-transcribe/fluidaudio` and
`engines/stt-diarize/fluidaudio` POST audio here and turn the JSON into
Dalston's typed responses. This process knows nothing about Dalston.

## Build and run

Requires an Apple Silicon Mac, macOS 15+, and Xcode command line tools.

```bash
swift build -c release
.build/release/fluidaudio-server
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FLUIDAUDIO_HOST` | `127.0.0.1` | Bind address. Keep loopback; the adapters run on the same Mac. |
| `FLUIDAUDIO_PORT` | `9200` | TCP port. |
| `FLUIDAUDIO_PRELOAD_ASR` | unset | Repo id to download/compile/load at startup. |
| `FLUIDAUDIO_PRELOAD_DIARIZER` | unset | `1` to load the diarizer at startup. |

Models download from Hugging Face on first use into FluidAudio's default
directory (`~/Library/Application Support/FluidAudio/Models`). The first
request for a model also pays the Core ML compile; preload to avoid that
on the first task.

## HTTP contract

```
GET  /health
  200 {"status":"ok","asr_loaded":"<repo id>"|null,"diarizer_loaded":bool,
       "fluidaudio_version":"0.17.7","device":"ane","queue_depth":0}

GET  /v1/capabilities
  200 {"server":"fluidaudio-server","version":"1.0.0",
       "asr_models":["FluidInference/parakeet-tdt-0.6b-v3-coreml","FluidInference/parakeet-ultra-coreml"],
       "diarizer_models":["FluidInference/speaker-diarization-coreml"],
       "languages":["bg","hr",...]}

POST /v1/transcribe   multipart/form-data
  file      (required) audio; 16 kHz mono WAV preferred, anything AVFoundation reads is accepted
  model     (optional) one of asr_models; default parakeet v3
  language  (optional) ISO 639-1; FluidAudio filters tokens by script, it does not force the language
  200 {"model":"...","duration":1234.5,"language":null,"text":"...",
       "words":[{"text":"hello","start":0.42,"end":0.71,"confidence":null},...],
       "timings":{"load_model_s":0.0,"transcribe_s":12.3}}

POST /v1/diarize      multipart/form-data
  file          (required)
  model         (optional) default speaker-diarization
  num_speakers  (optional) exact speaker count hint
  min_speakers / max_speakers  accepted, ignored, reported in "warnings"
  200 {"model":"...","duration":1234.5,
       "segments":[{"speaker":"SPEAKER_00","start":0.0,"end":4.2},...],
       "warnings":[],"timings":{"load_model_s":0.0,"diarize_s":40.1}}

Errors: 400 unknown model / bad upload, 503 inference queue full,
500 inference failure. Body: {"error":"..."}.
```

Model keys are the FluidAudio Hugging Face repo ids on purpose: Dalston's
transcribe stage hands engines the registry model's `source`, and the
`models/fluidaudio-*.yaml` entries set `source` to these strings.

## Concurrency

`Admission` serializes inference. One request runs and up to two wait (one
per stage); a third gets 503. The adapters are single-task runners and treat
503 as a task failure rather than retrying.

## Relationship to dalston-mobile

The model keys, the `int8V2` encoder workaround (FluidAudio issue #760),
token-timings-to-words and speaker renumbering were carried over from
dalston-mobile's `FluidTranscriptionEngine.swift`. The two codebases are
kept in step by hand; see the milestone spec for why they do not share a
package.

## Tests

```bash
swift test
```

Covers the pure conversions, model-key lookups and the admission queue.
Nothing downloads a model.
