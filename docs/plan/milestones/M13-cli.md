# M13: CLI Client

| | |
|---|---|
| **Goal** | Command-line tool for batch and real-time transcription |
| **Duration** | 2-3 days |
| **Dependencies** | M12 (Python SDK) |
| **Deliverable** | `dalston` CLI command with transcribe, listen, jobs, export, status |

## User Story

> *"As a developer or power user, I can transcribe audio files and capture live speech from the command line, with output that works for both humans and automated pipelines."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CLI ARCHITECTURE                                   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  dalston CLI                                                         │   │
│  │  ├── transcribe   Batch transcription (file/URL → transcript)       │   │
│  │  ├── listen       Real-time mic capture (live → streaming output)   │   │
│  │  ├── jobs         Job management (list, get, cancel, wait)          │   │
│  │  ├── export       Export transcript (SRT, VTT, TXT, JSON)           │   │
│  │  └── status       Server/system status                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  dalston-sdk (M12)                                                   │   │
│  │  ├── Dalston           Batch client (transcribe, export, jobs)      │   │
│  │  └── RealtimeSession   WebSocket streaming client                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Dalston Server                                                      │   │
│  │  ├── POST /v1/audio/transcriptions      Submit batch job            │   │
│  │  ├── GET  /v1/audio/transcriptions/{id} Job status/result           │   │
│  │  ├── WS   /v1/audio/transcriptions/stream  Real-time streaming      │   │
│  │  └── GET  /v1/realtime/status           System capacity             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Audio Capture (real-time mode):                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  sounddevice (PortAudio)                                             │   │
│  │  ├── macOS    ✓ Pre-built wheels                                    │   │
│  │  ├── Windows  ✓ Pre-built wheels                                    │   │
│  │  └── Linux    ✓ Requires libportaudio2                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Principles

1. **Simple defaults** — `dalston transcribe audio.mp3` just works
2. **Agent-friendly** — JSON output mode, predictable exit codes, machine-parseable
3. **Progressive disclosure** — Common options upfront, advanced options available
4. **Unix philosophy** — Composable with pipes, stdout for data, stderr for progress

---

## Commands

### Global Options

| Option | Short | Env Variable | Description |
|--------|-------|--------------|-------------|
| `--server` | `-s` | `DALSTON_SERVER` | Server URL (default: `http://localhost:8000`) |
| `--api-key` | `-k` | `DALSTON_API_KEY` | API key for authentication |
| `--verbose` | `-v` | | Verbose output to stderr |
| `--quiet` | `-q` | | Suppress non-essential output |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | File not found |
| 4 | Server unreachable |
| 5 | Transcription failed |

---

### `dalston transcribe` — Batch Transcription

```
dalston transcribe [OPTIONS] <FILE|URL>...
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--language` | `-l` | `auto` | Language code (`en`, `es`, `auto`) |
| `--output` | `-o` | stdout | Output file path |
| `--format` | `-f` | `txt` | Output: `txt`, `json`, `srt`, `vtt` |
| `--wait/--no-wait` | `-w` | wait | Wait for completion |
| `--json` | | | Machine-readable JSON output |
| `--speakers` | | `none` | `none`, `diarize`, `per-channel` |
| `--num-speakers` | | auto | Expected speaker count (1-32) |
| `--timestamps` | | `word` | `none`, `segment`, `word` |
| `--no-speakers` | | | Exclude speaker labels |

**Examples:**

```bash
dalston transcribe meeting.mp3
dalston transcribe meeting.mp3 -o transcript.txt
dalston transcribe podcast.mp3 -f srt --speakers diarize -o podcast.srt
dalston transcribe large.mp3 --no-wait --json   # Returns job ID
dalston transcribe *.mp3 -f json -o transcripts/
```

---

### `dalston listen` — Real-Time Transcription

```
dalston listen [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--language` | `-l` | `auto` | Language code |
| `--output` | `-o` | stdout | Output file (append mode) |
| `--format` | `-f` | `live` | Output: `live`, `json`, `jsonl` |
| `--model` | `-m` | `fast` | Model: `fast` or `accurate` |
| `--device` | `-d` | default | Audio input device |
| `--list-devices` | | | List devices and exit |
| `--no-interim` | | | Only show final transcripts |
| `--enhance` | | | Trigger batch enhancement on end |

**Output Formats:**

- `live` — Human-readable with timestamps
- `json` — Full session JSON on exit
- `jsonl` — JSON Lines, one per utterance (for pipelines)

**Examples:**

```bash
dalston listen
dalston listen -o notes.txt
dalston listen -f jsonl | jq -r '.text'
dalston listen --list-devices
```

**Live Output:**

```
[Listening... Press Ctrl+C to stop]

[00:02] Hello, welcome to today's meeting.
[00:05] We have three items on the agenda.

^C
[Session ended: 45.2s total, 32.1s speech]
```

---

### `dalston jobs` — Job Management

| Subcommand | Description |
|------------|-------------|
| `list` | List jobs with optional status filter |
| `get <id>` | Show job details |
| `cancel <id>` | Cancel pending/running job |
| `wait <id>` | Wait for completion, output result |

```bash
dalston jobs list
dalston jobs list --status running --json
dalston jobs get abc123
dalston jobs cancel abc123
dalston jobs wait abc123 -f srt -o output.srt
```

---

### `dalston export` — Export Transcripts

```
dalston export <JOB_ID> [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `txt` | `txt`, `json`, `srt`, `vtt` |
| `--output` | `-o` | stdout | Output file |
| `--no-speakers` | | | Exclude speaker labels |
| `--max-line-length` | | 42 | Subtitle line length |

```bash
dalston export abc123 -f srt -o subtitles.srt
dalston export abc123 -f json
```

---

### `dalston status` — System Status

```
dalston status [--json]
```

```
Server: http://localhost:8000 ✓

Batch Processing:
  Queue depth: 3 jobs pending

Real-time:
  Status: ready
  Capacity: 7/16 sessions (9 available)
  Workers: 2 active
```

---

## Agent Integration

Designed for easy scripting and LLM agent use:

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

---

## Configuration

**Environment Variables:**

```bash
DALSTON_SERVER=http://localhost:8000
DALSTON_API_KEY=dk_xxx
```

**Config File:** `~/.dalston/config.yaml` (optional)

```yaml
server: http://localhost:8000
api_key: dk_xxx
defaults:
  language: en
  format: txt
```

---

## Platform Requirements

### Audio Capture (listen command)

| Platform | Requirement |
|----------|-------------|
| macOS | Pre-built wheels (no action needed) |
| Windows | Pre-built wheels (no action needed) |
| Linux | `apt install libportaudio2` or equivalent |

---

## Steps

### 13.1: Package Structure

- Create `cli/` directory with `pyproject.toml`
- Package name: `dalston-cli`
- Dependencies: `dalston-sdk`, `click`, `rich`, `sounddevice`
- Entry point: `dalston` command

### 13.2: CLI Framework

- Global options (server, api-key, verbose, quiet)
- Config loading: defaults → file → env → CLI args
- Error handling with exit codes
- Version command

### 13.3: Transcribe Command

- File and URL input handling
- Progress display during wait
- All output formats
- Multiple file handling
- JSON mode for agents

### 13.4: Listen Command

- Cross-platform microphone capture
- Device listing and selection
- Live/JSONL output modes
- Graceful Ctrl+C with session summary

### 13.5: Jobs Command

- `list`, `get`, `cancel`, `wait` subcommands
- Table output with rich
- JSON mode

### 13.6: Export Command

- All export formats
- Speaker and subtitle options

### 13.7: Status Command

- Server health check
- Batch and realtime status
- Human and JSON output

### 13.8: Output Formatting

- Consistent formatting module
- Rich for human output
- Clean JSON for machine output

### 13.9: Documentation

- README with installation and usage
- Help text for all commands

---

## Verification

```bash
# Install
pip install -e ./cli

# Batch transcription
dalston transcribe test.mp3
dalston transcribe test.mp3 --speakers diarize -f srt -o test.srt
dalston transcribe test.mp3 --no-wait --json

# Real-time
dalston listen --list-devices
dalston listen
dalston listen -f jsonl

# Jobs
dalston jobs list
dalston jobs get <id>

# Status
dalston status
dalston status --json
```

---

## Checkpoint

- [ ] Package structure with entry point
- [ ] CLI framework with global options
- [ ] `transcribe` command with all formats
- [ ] `listen` command with cross-platform audio
- [ ] `jobs` command with subcommands
- [ ] `export` command
- [ ] `status` command
- [ ] Output formatting with rich
- [ ] Configuration support
- [ ] Documentation

---

## Future Enhancements

1. **Shell completion** — Bash/Zsh/Fish autocompletion
2. **Interactive mode** — TUI with live waveform
3. **Watch mode** — Monitor directory for new files
4. **Batch manifest** — Process multiple files from config
