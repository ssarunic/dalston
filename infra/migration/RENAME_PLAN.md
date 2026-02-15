# Docker Container Rename Migration Plan

## Overview

Rename Docker service names from `engine-*` / `realtime-*` convention to the new `{type}-{domain}-{stage}-{impl}` convention.

**Key point**: Engine IDs (used in Redis queues, task records) remain unchanged. Only Docker service names change.

## Pre-Migration Checklist

- [ ] All batch queues are empty (`docker compose exec redis redis-cli KEYS "dalston:queue:*"`)
- [ ] No active realtime sessions (`docker compose exec redis redis-cli SCARD "dalston:realtime:workers"`)
- [ ] Backup current docker-compose.yml
- [ ] Notify team of planned maintenance window

---

## Phase 1: Tag Existing Images (No Downtime)

Tag current images with new names so we don't need to rebuild.

```bash
#!/bin/bash
# Run from project root

# Get current image names from running containers
docker tag dalston-engine-audio-prepare dalston-stt-batch-prepare
docker tag dalston-engine-faster-whisper dalston-stt-batch-transcribe-whisper
docker tag dalston-engine-parakeet dalston-stt-batch-transcribe-parakeet
docker tag dalston-engine-whisperx-align dalston-stt-batch-align-whisperx
docker tag dalston-engine-pyannote-3.1 dalston-stt-batch-diarize-pyannote-v31
docker tag dalston-engine-pyannote-4.0 dalston-stt-batch-diarize-pyannote-v40
docker tag dalston-engine-pii-presidio dalston-stt-batch-detect-presidio
docker tag dalston-engine-audio-redactor dalston-stt-batch-redact-audio
docker tag dalston-engine-final-merger dalston-stt-batch-merge
docker tag dalston-realtime-whisper dalston-stt-rt-transcribe-whisper
docker tag dalston-realtime-parakeet dalston-stt-rt-transcribe-parakeet

echo "Images tagged. Verify with: docker images | grep dalston"
```

---

## Phase 2: Update Configuration Files

### 2.1 docker-compose.yml

Replace service names. Example transformation:

```yaml
# Before
services:
  engine-faster-whisper:
    build: ./engines/transcribe/faster-whisper
    environment:
      - ENGINE_ID=faster-whisper
    ...

# After
services:
  stt-batch-transcribe-whisper:
    build: ./engines/transcribe/faster-whisper
    image: dalston-stt-batch-transcribe-whisper  # Use tagged image
    environment:
      - ENGINE_ID=faster-whisper  # ENGINE_ID unchanged!
    ...
```

### 2.2 Realtime Workers - Update WORKER_ID

```yaml
# Before
services:
  realtime-whisper-1:
    environment:
      - WORKER_ID=realtime-whisper-1

# After
services:
  stt-rt-transcribe-whisper-1:
    environment:
      - WORKER_ID=stt-rt-transcribe-whisper-1  # Must match service name
```

### 2.3 Prometheus Configuration

Update `docker/prometheus/prometheus.yml` targets:

```yaml
# Before
- targets:
  - 'engine-faster-whisper:9100'
  - 'realtime-whisper-1:9100'

# After
- targets:
  - 'stt-batch-transcribe-whisper:9100'
  - 'stt-rt-transcribe-whisper-1:9100'
```

### 2.4 AWS Override File

Update `infra/docker/docker-compose.aws.yml` with new service names.

### 2.5 Documentation

Update container references in:

- CLAUDE.md
- docs/*.md files

---

## Phase 3: Migration Execution

### Option A: Rolling Migration (Minimal Downtime)

For each service type:

```bash
# 1. Stop old batch engines (new jobs will queue)
docker compose stop engine-audio-prepare engine-faster-whisper engine-whisperx-align \
  engine-pyannote-3.1 engine-final-merger

# 2. Start new batch engines (with updated docker-compose.yml)
docker compose up -d stt-batch-prepare stt-batch-transcribe-whisper stt-batch-align-whisperx \
  stt-batch-diarize-pyannote-v31 stt-batch-merge

# 3. Verify batch engines are processing
docker compose logs -f stt-batch-transcribe-whisper

# 4. Stop old realtime workers (sessions will gracefully close)
docker compose stop realtime-whisper-1 realtime-whisper-2

# 5. Start new realtime workers
docker compose up -d stt-rt-transcribe-whisper-1 stt-rt-transcribe-whisper-2

# 6. Remove old containers
docker compose rm -f engine-audio-prepare engine-faster-whisper ...
```

### Option B: Full Restart (Clean Cutover)

```bash
# 1. Stop all services
docker compose down

# 2. Apply updated docker-compose.yml (already done in Phase 2)

# 3. Start all services with new names
docker compose up -d

# 4. Verify health
curl http://localhost:8000/health
curl http://localhost:8000/v1/system/status
```

---

## Phase 4: Cleanup

```bash
# Remove old image tags (optional, saves disk space)
docker rmi dalston-engine-audio-prepare dalston-engine-faster-whisper ...

# Verify no old containers running
docker ps | grep -E "engine-|realtime-"

# Clean up any orphaned volumes
docker volume prune -f
```

---

## Rollback Plan

If issues occur:

```bash
# 1. Restore backup docker-compose.yml
cp docker-compose.yml.bak docker-compose.yml

# 2. Stop new containers
docker compose down

# 3. Start with old configuration
docker compose up -d
```

---

## Verification Checklist

- [ ] `docker compose ps` shows all new service names
- [ ] `curl http://localhost:8000/health` returns healthy
- [ ] Submit test batch job, verify completion
- [ ] Connect realtime WebSocket, verify transcription
- [ ] Prometheus targets showing metrics (check Grafana)
- [ ] No errors in `docker compose logs`

---

## Notes

### What Stays the Same

| Item | Example | Reason |
|------|---------|--------|
| Engine IDs | `faster-whisper` | Used in Redis keys, task records |
| Redis queue keys | `dalston:queue:faster-whisper` | Engine ID based, not container name |
| Engine config files | `engines/*/engine.yaml` | Internal engine identity |
| Model registry mappings | `whisper-large-v3 → faster-whisper` | Engine ID based |

### What Changes

| Item | Before | After |
|------|--------|-------|
| Docker service names | `engine-faster-whisper` | `stt-batch-transcribe-whisper` |
| Container hostnames | `engine-faster-whisper` | `stt-batch-transcribe-whisper` |
| Prometheus targets | `engine-faster-whisper:9100` | `stt-batch-transcribe-whisper:9100` |
| WORKER_ID env var | `realtime-whisper-1` | `stt-rt-transcribe-whisper-1` |
| Worker registry in Redis | `dalston:realtime:worker:realtime-whisper-1` | `dalston:realtime:worker:stt-rt-transcribe-whisper-1` |
