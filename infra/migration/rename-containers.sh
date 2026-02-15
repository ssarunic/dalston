#!/bin/bash
# Docker Container Rename Script
# This script documents the migration from old naming to new naming convention
#
# NAMING CONVENTION:
#   {domain}-{type}-{stage}-{impl}[-v{version}][-cpu]
#   - domain: stt (speech-to-text), tts (text-to-speech, future)
#   - type: batch or rt (realtime)
#   - stage: prepare, transcribe, align, diarize, detect, redact, merge
#   - impl: whisper, parakeet, pyannote, presidio, etc.
#   - version: v31, v40, etc. (only when multiple versions coexist)
#   - cpu: only when forcing CPU mode (GPU is default)
#
# Run from project root: ./infra/migration/rename-containers.sh

set -e

# Mapping arrays (old -> new)
declare -A BATCH_MAPPING=(
    ["engine-audio-prepare"]="stt-batch-prepare"
    ["engine-faster-whisper"]="stt-batch-transcribe-whisper"
    ["engine-faster-whisper-gpu"]="stt-batch-transcribe-whisper"
    ["engine-parakeet"]="stt-batch-transcribe-parakeet"
    ["engine-parakeet-cpu"]="stt-batch-transcribe-parakeet-cpu"
    ["engine-whisperx-align"]="stt-batch-align-whisperx"
    ["engine-whisperx-align-gpu"]="stt-batch-align-whisperx"
    ["engine-pyannote-3.1"]="stt-batch-diarize-pyannote-v31"
    ["engine-pyannote-3.1-gpu"]="stt-batch-diarize-pyannote-v31"
    ["engine-pyannote-4.0"]="stt-batch-diarize-pyannote-v40"
    ["engine-pyannote-4.0-gpu"]="stt-batch-diarize-pyannote-v40"
    ["engine-pii-presidio"]="stt-batch-detect-presidio"
    ["engine-audio-redactor"]="stt-batch-redact-audio"
    ["engine-final-merger"]="stt-batch-merge"
)

declare -A REALTIME_MAPPING=(
    # Parakeet (default for local dev, scalable)
    ["realtime-parakeet-cpu"]="stt-rt-transcribe-parakeet-cpu"
    ["realtime-parakeet-gpu"]="stt-rt-transcribe-parakeet"
    # Whisper (requires --profile whisper for CPU, scalable)
    ["realtime-whisper-1"]="stt-rt-transcribe-whisper-cpu"
    ["realtime-whisper-gpu"]="stt-rt-transcribe-whisper"
)

print_mapping() {
    echo "=== Container Name Mapping ==="
    echo ""
    echo "BATCH ENGINES:"
    printf "%-30s -> %s\n" "OLD" "NEW"
    echo "----------------------------------------"
    for old in $(echo "${!BATCH_MAPPING[@]}" | tr ' ' '\n' | sort); do
        printf "%-30s -> %s\n" "$old" "${BATCH_MAPPING[$old]}"
    done

    echo ""
    echo "REALTIME WORKERS:"
    printf "%-30s -> %s\n" "OLD" "NEW"
    echo "----------------------------------------"
    for old in $(echo "${!REALTIME_MAPPING[@]}" | tr ' ' '\n' | sort); do
        printf "%-30s -> %s\n" "$old" "${REALTIME_MAPPING[$old]}"
    done

    echo ""
    echo "IMAGE NAMING:"
    echo "  dalston/stt-batch-{stage}-{impl}:latest"
    echo "  dalston/stt-rt-transcribe-{impl}:latest"
    echo ""
    echo "Examples:"
    echo "  dalston/stt-batch-transcribe-whisper:latest"
    echo "  dalston/stt-batch-diarize-pyannote-v31:latest"
    echo "  dalston/stt-rt-transcribe-whisper:latest"
}

print_current() {
    echo "=== Current Containers ==="
    docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "dalston|NAMES"

    echo ""
    echo "=== Current Images ==="
    docker images --format "{{.Repository}}:{{.Tag}}" | grep "dalston/stt-" | sort
}

# Main
case "${1:-}" in
    "mapping")
        print_mapping
        ;;
    "current")
        print_current
        ;;
    *)
        echo "Usage: $0 {mapping|current}"
        echo ""
        echo "Commands:"
        echo "  mapping  - Show old -> new name mapping"
        echo "  current  - Show currently running containers and images"
        echo ""
        echo "NOTE: Migration has been completed. This script is for reference only."
        ;;
esac
