# Dalston

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Ollama for ASR.** Run open-source speech recognition models on your machine or private cloud. Freedom from proprietary APIs, full privacy, no quality compromise.

## Why Dalston

**Pluggable and extensible** — Mix and match transcription, alignment, diarization, and PII detection models. Swap components without breaking your pipeline. Completely open source and free.

**Drop-in integration** — OpenAI and ElevenLabs compatible APIs mean you can point your existing code at Dalston and it just works. Need more power? The native Dalston API unlocks advanced functionality like multi-engine routing, pipeline customization, and detailed engine metadata.

## What It Does

Transcribe audio files or live streams with speaker diarization, word-level timestamps, and GPU acceleration. Run it on your own infrastructure.

```bash
# One-command local transcription (M57 zero-config bootstrap)
# - auto-starts local server if missing
# - auto-ensures default model (distil-small)
DALSTON_SECURITY_MODE=none dalston transcribe tests/audio/test_merged.wav --format json
```

```json
{
  "text": "Hello, welcome to the meeting...",
  "segments": [
    {"speaker": "SPEAKER_01", "start": 0.0, "end": 2.5, "text": "Hello, welcome to the meeting."},
    {"speaker": "SPEAKER_02", "start": 2.8, "end": 5.1, "text": "Thanks for having me."}
  ]
}
```

## Quick Start

```bash
git clone https://github.com/ssarunic/dalston.git
cd dalston
pip install -e ".[gateway,orchestrator,dev]"
pip install -e ./sdk -e ./cli
DALSTON_SECURITY_MODE=none dalston transcribe tests/audio/test_merged.wav --format json
```

For distributed Docker deployments, see the [deployment guide](docs/guides/self-hosted-deployment-tutorial.md).

## Features

- **Batch & Real-time** — File uploads or WebSocket streaming
- **Speaker Diarization** — Identify who said what
- **Word Timestamps** — Precise timing for every word
- **OpenAI & ElevenLabs Compatible** — Drop-in replacement for existing integrations
- **Modular Engines** — Faster Whisper, WhisperX, Pyannote, and more
- **Private by Default** — Runs entirely on your infrastructure, no data leaves your environment

## Documentation

- [Architecture](docs/specs/ARCHITECTURE.md)
- [REST API](docs/specs/batch/API.md)
- [WebSocket API](docs/specs/realtime/WEBSOCKET_API.md)
- [Deployment Guide](docs/guides/self-hosted-deployment-tutorial.md)

## License

[Apache 2.0](LICENSE)
