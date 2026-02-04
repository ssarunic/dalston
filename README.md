# Dalston

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Self-hosted audio transcription server with ElevenLabs-compatible API.

## What It Does

Transcribe audio files or live streams with speaker diarization, word-level timestamps, and GPU acceleration. Run it on your own infrastructure.

```bash
# Transcribe a file
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "speaker_detection=diarize"
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
docker compose up -d
```

The API is available at `http://localhost:8000`. See the [deployment guide](docs/guides/self-hosted-deployment-tutorial.md) for production setup.

## Features

- **Batch & Real-time** — File uploads or WebSocket streaming
- **Speaker Diarization** — Identify who said what
- **Word Timestamps** — Precise timing for every word
- **ElevenLabs Compatible** — Drop-in replacement for `/v1/speech-to-text`
- **Modular Engines** — Faster Whisper, WhisperX, Pyannote, and more

## Documentation

- [Architecture](docs/specs/ARCHITECTURE.md)
- [REST API](docs/specs/batch/API.md)
- [WebSocket API](docs/specs/realtime/WEBSOCKET_API.md)
- [Deployment Guide](docs/guides/self-hosted-deployment-tutorial.md)

## License

[Apache 2.0](LICENSE)
