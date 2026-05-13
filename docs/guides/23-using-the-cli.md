# Using the `dalston` CLI

> One command transcribes a file. One command listens to your mic. One
> command tells you the server is healthy. The CLI is the fastest path from
> "audio sitting on disk" to "transcript on stdout."

```bash
pip install -e ./cli
```

The CLI is built on Typer. Run `dalston --help` for the auto-generated tour.
This page covers the most useful commands with realistic examples.

---

## Configuration

Three places, in priority order:

1. CLI flags: `--server`, `--api-key`
2. Environment: `DALSTON_SERVER`, `DALSTON_API_KEY`
3. Config file: `~/.dalston/config.yaml`

```bash
export DALSTON_SERVER=https://dalston-control-plane.<your-tailnet>.ts.net
export DALSTON_API_KEY=dk_...
```

Or the YAML form:

```yaml
# ~/.dalston/config.yaml
server: https://dalston-control-plane.tail-xyz.ts.net
api_key: dk_...
```

---

## Top-level commands

```
dalston transcribe [FILES] [OPTIONS]   # batch transcribe one or more files
dalston listen     [OPTIONS]           # real-time microphone capture
dalston export     JOB_ID  [OPTIONS]   # download a transcript in another format
dalston status                         # health check
dalston engines                        # list registered engines
dalston jobs       (list|get|wait|cancel|delete)
dalston sessions   (list|get|...)      # real-time sessions
dalston models     (list|...)
dalston server     (start|stop|...)    # local lite mode helpers
```

---

## `dalston transcribe` — the headline command

Source: [`cli/dalston_cli/commands/transcribe.py`](../../cli/dalston_cli/commands/transcribe.py).

```bash
# Simplest: one file, auto model, auto language
dalston transcribe meeting.mp3

# Pick an engine + language explicitly
dalston transcribe meeting.mp3 --model faster-whisper --language en

# With diarization and word timestamps
dalston transcribe meeting.mp3 --speakers diarize --timestamps word

# Hint speaker count
dalston transcribe interview.mp3 --speakers diarize --num-speakers 2

# SRT subtitles
dalston transcribe lecture.mp4 --format srt -o lecture.srt

# Show word timestamps in the printed output
dalston transcribe meeting.mp3 --show-words

# Boost domain vocabulary (engineering jargon)
dalston transcribe standup.mp3 -v "PostgreSQL" -v "Kubernetes" -v "Tailscale"

# Multiple files at once (output to a directory)
dalston transcribe *.mp3 -o ./transcripts/

# From a URL (HTTPS, S3 presigned, GDrive, Dropbox)
dalston transcribe --url https://example.com/audio.mp3

# JSON output for scripting
dalston transcribe meeting.mp3 --json | jq '.transcript.text'

# Don't wait, just submit and exit
dalston transcribe meeting.mp3 --no-wait
# → prints job_id; poll later with `dalston jobs get <id>`
```

Common flags:

| Flag | What |
|---|---|
| `-m`, `--model` | Engine ID or `auto` (default) |
| `-l`, `--language` | Language code or `auto` (default) |
| `-v`, `--vocab` | Term to boost (repeatable) |
| `-o`, `--output` | Output file (or directory for multi-file) |
| `-f`, `--format` | `txt`, `json`, `srt`, `vtt` |
| `--speakers` | `none` (default), `diarize`, `per-channel` |
| `--num-speakers`, `--min-speakers`, `--max-speakers` | Speaker count hints |
| `--timestamps` | `none`, `segment`, `word` (default) |
| `--show-words` | Print word-level timing in `txt` output |
| `--wait` / `--no-wait` | Block until done (default) or just submit |
| `--json` | Machine-readable output |

---

## `dalston listen` — real-time microphone

Source: [`cli/dalston_cli/commands/listen.py`](../../cli/dalston_cli/commands/listen.py).

```bash
# Capture from default mic, stream to stdout
dalston listen

# List input devices
dalston listen --list-devices

# Pick a specific device
dalston listen --device "MacBook Pro Microphone"

# English only, save the captured audio + transcript
dalston listen --language en --store-audio --store-transcript

# Replay a file as if it were a live stream (for testing)
dalston listen --input-file recorded.wav
```

Outputs partial transcripts as you speak (interim results), then "commits"
final transcripts on silence (VAD events drive this — see
[40-realtime-overview.md](40-realtime-overview.md)).

---

## `dalston jobs` — manage submitted jobs

```bash
dalston jobs list                       # paginated list
dalston jobs list --status running
dalston jobs list --limit 100 --json

# Filter by created_at; --since accepts ISO 8601, a relative offset
# (90m / 24h / 7d), 'today' (UTC midnight), or 'yesterday'.
dalston jobs list --since 24h
dalston jobs list --since today --limit 100
dalston jobs list --since 2026-05-13T17:23:00Z

dalston jobs get JOB_ID                 # full detail
dalston jobs wait JOB_ID                # block until completed/failed
dalston jobs cancel JOB_ID
dalston jobs delete JOB_ID              # also removes S3 artifacts
```

The table summarizes total audio across the rows in its footer
(e.g. `36 jobs, 36 with duration — total audio: 31h26m (31.44h)`),
useful for back-of-envelope cost-per-audio-hour calculations.

---

## `dalston export` — alternate formats

A job's transcript is stored once; export converts on read.

```bash
dalston export JOB_ID -f srt -o subtitles.srt
dalston export JOB_ID -f vtt
dalston export JOB_ID -f json   # full JSON with words and speakers
```

---

## `dalston engines` — what's running

```bash
dalston engines
# ┌─────────────────────────┬─────────┬────────┬──────────┬──────────────┐
# │ engine_id               │ stage   │ status │ capacity │ models loaded│
# ├─────────────────────────┼─────────┼────────┼──────────┼──────────────┤
# │ faster-whisper          │ trans.. │ ready  │ 4        │ large-v3-tu..│
# │ pyannote-4.0            │ diarize │ ready  │ 1        │ comm-1       │
# │ phoneme-align           │ align   │ ready  │ 4        │ wav2vec2-..  │
# └─────────────────────────┴─────────┴────────┴──────────┴──────────────┘
```

---

## `dalston status` — health check

```bash
dalston status
# server: http://127.0.0.1:8000
# state:  healthy
# version: ...
```

If something is wrong, this is your first stop.

---

## `dalston sessions` — real-time

```bash
dalston sessions list                  # active + recent sessions
dalston sessions get SESSION_ID
```

---

## `dalston models` — model catalog

```bash
dalston models list
dalston models list --stage transcribe
```

---

## `dalston server` — local lite mode

Source: [`cli/dalston_cli/commands/server.py`](../../cli/dalston_cli/commands/server.py).

For lite-mode setups (single-process, no Docker), this manages the local
server lifecycle. Most users don't need it — they use `make dev` or
`dalston-aws launch` instead.

---

## Scripting recipes

**Transcribe a directory of files in parallel** (xargs respects `-P`):

```bash
ls audio/*.mp3 | xargs -n1 -P4 -I{} dalston transcribe {} -o transcripts/ -f json
```

**Submit and check later** (CI use-case):

```bash
JOB_ID=$(dalston transcribe big_file.mp3 --no-wait --json | jq -r '.id')
echo $JOB_ID
# ...
dalston jobs wait $JOB_ID
dalston export $JOB_ID -f srt -o big_file.srt
```

**Daily catalog batch** (cron):

```bash
#!/bin/bash
for f in /podcast/incoming/*.mp3; do
  dalston transcribe "$f" \
    --model nemo \
    --speakers diarize \
    --timestamps word \
    --format json \
    -o "/podcast/transcripts/$(basename "$f" .mp3).json"
done
```

---

## See also

- [01-quickstart.md](01-quickstart.md) — start here
- [24-using-the-python-sdk.md](24-using-the-python-sdk.md) — programmatic API
- [22-using-the-web-console.md](22-using-the-web-console.md) — visual companion
- [40-realtime-overview.md](40-realtime-overview.md) — what `dalston listen` does under the hood
