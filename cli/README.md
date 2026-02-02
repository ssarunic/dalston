# Dalston CLI

Command-line client for Dalston transcription server.

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
| `--language` | `-l` | `auto` | Language code or 'auto' |
| `--output` | `-o` | stdout | Output file path |
| `--format` | `-f` | `txt` | Output: `txt`, `json`, `srt`, `vtt` |
| `--wait/--no-wait` | `-w` | wait | Wait for completion |
| `--json` | | | Machine-readable JSON output |
| `--speakers` | | `none` | `none`, `diarize`, `per-channel` |
| `--num-speakers` | | auto | Expected speaker count (1-32) |
| `--timestamps` | | `word` | `none`, `segment`, `word` |

**Examples:**

```bash
dalston transcribe meeting.mp3
dalston transcribe meeting.mp3 -o transcript.txt
dalston transcribe podcast.mp3 -f srt --speakers diarize -o podcast.srt
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
| `--model` | `-m` | `fast` | `fast` or `accurate` |
| `--device` | `-d` | default | Audio input device |
| `--list-devices` | | | List devices and exit |
| `--no-interim` | | | Only show final transcripts |

**Examples:**

```bash
dalston listen
dalston listen -o notes.txt
dalston listen -f jsonl | jq -r '.text'
dalston listen --list-devices
```

### `dalston jobs`

Manage transcription jobs.

```bash
dalston jobs list [--status STATUS] [--limit N] [--json]
dalston jobs get JOB_ID [--json]
dalston jobs wait JOB_ID [-f FORMAT] [-o OUTPUT]
```

**Examples:**

```bash
dalston jobs list
dalston jobs list --status running --json
dalston jobs get abc123
dalston jobs wait abc123 -f srt -o output.srt
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
defaults:
  language: en
  format: txt
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | File not found |
| 4 | Server unreachable |
| 5 | Transcription failed |

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
