# Dalston CLI

Command-line client for a Dalston transcription server.

## Installation

```bash
pip install dalston-cli
```

Or install from source:

```bash
cd cli
pip install -e .
```

### Platform Requirements

For real-time microphone capture (`dalston listen`):

| Platform | Requirement |
|----------|-------------|
| macOS | Pre-built wheels (no action needed) |
| Windows | Pre-built wheels (no action needed) |
| Linux | `apt install libportaudio2` or equivalent |

## Quick Start

```bash
# Point the client at an existing deployment
export DALSTON_SERVER=http://localhost:8000
export DALSTON_API_KEY=dk_...

# Transcribe an audio file
dalston transcribe meeting.mp3

# Real-time transcription from microphone
dalston listen

# Check server status
dalston status
```

## Commands

### `dalston transcribe`

Batch transcription of audio files.

```bash
dalston transcribe [OPTIONS] FILES...
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--url` | | | Transcribe an HTTPS, S3, Google Drive, or Dropbox URL instead of a file |
| `--model` | `-m` | `auto` | Engine/model ID or automatic selection |
| `--language` | `-l` | `auto` | Language code or 'auto' |
| `--vocab` | `-v` | | Vocabulary term; repeat for multiple terms |
| `--output` | `-o` | stdout | Output file path |
| `--format` | `-f` | `txt` | `txt` or `json`; use `dalston export` for subtitles |
| `--wait/--no-wait` | `-w` | wait | Wait for completion |
| `--json` | | | Machine-readable JSON output |
| `--speakers` | | `none` | `none`, `diarize`, `per-channel` |
| `--num-speakers` | | auto | Expected speaker count (1-32) |
| `--min-speakers`, `--max-speakers` | | auto | Diarization bounds |
| `--timestamps` | | `word` | `none`, `segment`, `word` |
| `--show-words` | | off | Show word timing in text output |
| `--pii` | | off | Detect PII |
| `--redact-audio` | | off | Generate redacted audio |
| `--redaction-mode` | | | `silence` or `beep` |
| `--retention` | `-r` | server default | `0` transient, `-1` permanent, or days |
| `--profile` | | `core` | Lite profile: `core`, `speaker`, `compliance` |

**Examples:**

```bash
dalston transcribe meeting.mp3
dalston transcribe meeting.mp3 -o transcript.txt
JOB_ID=$(dalston transcribe podcast.mp3 --speakers diarize --no-wait --json | jq -r '.id')
dalston jobs wait "$JOB_ID"
dalston export "$JOB_ID" -f srt -o podcast.srt
dalston transcribe call.mp3 --pii --redact-audio --redaction-mode beep
dalston transcribe --url https://example.com/audio.mp3
dalston transcribe large.mp3 --no-wait --json
```

### `dalston listen`

Real-time transcription from microphone.

```bash
dalston listen [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--language` | `-l` | `auto` | Language code |
| `--output` | `-o` | stdout | Output file (append mode) |
| `--format` | `-f` | `live` | `live`, `json`, `jsonl` |
| `--model` | `-m` | automatic | A registered realtime model; omit for any ready worker |
| `--device` | `-d` | default | Audio input device |
| `--list-devices` | | | List devices and exit |
| `--no-interim` | | | Only show final transcripts |
| `--no-vad` | | | Disable VAD events |
| `--pii` | | off | Enable realtime PII detection |
| `--redact-audio` | | off | Generate redacted audio |
| `--vocabulary` | `-V` | | Comma-separated or JSON vocabulary |
| `--file` | `-i` | | Stream a file instead of the microphone |

**Examples:**

```bash
dalston listen
dalston listen -o notes.txt
dalston listen -f jsonl | jq -r '.text'
dalston listen --list-devices
dalston listen -i recorded.wav
```

The CLI retains legacy `--store-audio` and `--store-transcript` switches for
compatibility. The current native gateway derives persistence from session
retention, so these switches do not override server policy.

### `dalston jobs`

Manage transcription jobs.

```bash
dalston jobs list [--status STATUS] [--limit N] [--json]
dalston jobs get JOB_ID [--json]
dalston jobs wait JOB_ID [-f FORMAT] [-o OUTPUT]
dalston jobs cancel JOB_ID
```

**Examples:**

```bash
dalston jobs list
dalston jobs list --status running --json
dalston jobs list --since 24h
dalston jobs get abc123
dalston jobs wait abc123
dalston export abc123 -f srt -o output.srt
dalston jobs cancel abc123
```

### `dalston export`

Export transcript in various formats.

```bash
dalston export JOB_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `txt` | `txt`, `json`, `srt`, `vtt` |
| `--output` | `-o` | stdout | Output file |
| `--no-speakers` | | | Exclude speaker labels |
| `--max-line-length` | | 42 | Subtitle line length |
| `--max-lines` | | 2 | Maximum subtitle lines per block |

**Examples:**

```bash
dalston export abc123 -f srt -o subtitles.srt
dalston export abc123 -f json
```

### `dalston status`

Show server and system status.

```bash
dalston status [--json]
```

### Other command groups

```bash
dalston engines
dalston sessions list
dalston sessions get SESSION_ID
dalston sessions delete SESSION_ID
dalston models list
dalston models list --engine_id nemo
dalston models pull MODEL_ID
dalston server status
dalston server stop
```

`dalston transcribe` may start a local ghost server automatically when the
target is the default localhost URL and the Dalston backend dependencies are
installed. The `dalston-cli` package by itself is only a client; otherwise use
`make dev` or configure an existing `DALSTON_SERVER`.

## Configuration

### Environment Variables

```bash
DALSTON_SERVER=http://localhost:8000
DALSTON_API_KEY=dk_xxx
```

### Config File

`~/.dalston/config.yaml`:

```yaml
server: http://localhost:8000
api_key: dk_xxx
```

The configuration loader currently reads a `defaults` mapping, but commands do
not consume it. Use flags until command-level defaults are implemented.

## Exit behavior

The CLI returns zero on success and non-zero on command, validation, network,
or transcription errors. Do not depend on undocumented per-error numeric codes.

## Agent Integration

Designed for scripting and LLM agent use:

```bash
# Submit and capture job ID
JOB_ID=$(dalston transcribe audio.mp3 --no-wait --json | jq -r '.id')

# Check status
dalston jobs get $JOB_ID --json | jq '.status'

# Real-time with processing
dalston listen -f jsonl | while read -r line; do
  echo "$line" | jq -r '.text'
done

# Check capacity before starting
dalston status --json | jq '.realtime.available_capacity'
```
