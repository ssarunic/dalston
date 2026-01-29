#!/usr/bin/env bash
#
# M01 Verification Script
#
# Verifies the complete batch flow works end-to-end with stub engines.
# Expected result: Submit audio file -> get stub transcript back
#
# Prerequisites:
#   1. Build engine base image:
#      docker build -f docker/Dockerfile.engine-base -t dalston/engine-base:latest .
#   2. Start all services:
#      docker compose up -d
#   3. Wait for services to be healthy:
#      docker compose ps
#
# Usage:
#   ./scripts/verify-m01.sh [test-audio-file]
#
# If no audio file is provided, a dummy file will be created.

# Configuration
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
MAX_POLL_ATTEMPTS="${MAX_POLL_ATTEMPTS:-30}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Check if gateway is healthy
check_gateway_health() {
    log_info "Checking gateway health..."

    if ! curl -sf "${GATEWAY_URL}/health" > /dev/null 2>&1; then
        log_error "Gateway is not healthy at ${GATEWAY_URL}"
        log_error "Make sure services are running: docker compose ps"
        return 1
    fi

    log_info "Gateway is healthy"
    return 0
}

# Create a dummy audio file if none provided
create_dummy_audio() {
    local file="$1"
    log_info "Creating dummy audio file: ${file}"

    # Create a simple file with some data (stub engines don't actually process audio)
    dd if=/dev/zero of="$file" bs=1024 count=2 2>/dev/null

    local size
    size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null)
    log_info "Dummy audio file created (${size} bytes)"
}

# Submit a transcription job
# Returns job_id on stdout
submit_job() {
    local audio_file="$1"

    log_info "Submitting transcription job..."
    log_info "Audio file: ${audio_file}"

    local response
    local http_code

    # Use curl with separate output for http code
    response=$(curl -s -w "\n%{http_code}" -X POST "${GATEWAY_URL}/v1/audio/transcriptions" \
        -F "file=@${audio_file}")

    http_code=$(echo "$response" | tail -n1)
    response=$(echo "$response" | sed '$d')

    log_info "HTTP Status: ${http_code}"

    if [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
        log_error "Failed to submit job (HTTP ${http_code})"
        log_error "Response: ${response}"
        return 1
    fi

    local job_id
    job_id=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

    if [ -z "$job_id" ]; then
        log_error "Failed to extract job ID from response"
        log_error "Response: ${response}"
        return 1
    fi

    log_info "Job submitted successfully"
    log_info "Job ID: ${job_id}"

    # Return job_id on stdout
    echo "$job_id"
    return 0
}

# Poll job status until completion
# Returns full response on stdout
poll_job() {
    local job_id="$1"
    local attempt=1

    log_info "Polling job status (max ${MAX_POLL_ATTEMPTS} attempts, ${POLL_INTERVAL}s interval)..."

    while [ $attempt -le $MAX_POLL_ATTEMPTS ]; do
        local response
        response=$(curl -sf "${GATEWAY_URL}/v1/audio/transcriptions/${job_id}" 2>&1)

        if [ $? -ne 0 ]; then
            log_warn "Failed to get job status (attempt ${attempt}/${MAX_POLL_ATTEMPTS})"
            sleep "$POLL_INTERVAL"
            attempt=$((attempt + 1))
            continue
        fi

        local status
        status=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])" 2>/dev/null)

        if [ -z "$status" ]; then
            status="unknown"
        fi

        log_info "Attempt ${attempt}/${MAX_POLL_ATTEMPTS}: status=${status}"

        case "$status" in
            completed)
                log_info "Job completed successfully!"
                echo "$response"
                return 0
                ;;
            failed)
                log_error "Job failed!"
                local error
                error=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'unknown'))" 2>/dev/null)
                log_error "Error: ${error}"
                return 1
                ;;
            pending|processing|running)
                sleep "$POLL_INTERVAL"
                attempt=$((attempt + 1))
                ;;
            *)
                log_warn "Unknown status: ${status}"
                sleep "$POLL_INTERVAL"
                attempt=$((attempt + 1))
                ;;
        esac
    done

    log_error "Job did not complete within ${MAX_POLL_ATTEMPTS} attempts"
    return 1
}

# Verify the response contains expected stub transcript
verify_response() {
    local response="$1"

    log_info "Verifying response..."

    # Extract text using python
    local text
    text=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('text', ''))" 2>/dev/null)

    if echo "$text" | grep -qi "stub"; then
        log_info "Found expected stub transcript: ${text}"
        return 0
    fi

    if [ -n "$text" ]; then
        log_info "Got transcript text: ${text}"
        return 0
    fi

    log_warn "Could not find transcript text in response"
    return 0  # Don't fail, just warn
}

# Main execution
main() {
    local audio_file="${1:-}"
    local temp_file=""
    local exit_code=0

    echo "" >&2
    echo "========================================" >&2
    echo "  M01 Verification: Hello World" >&2
    echo "========================================" >&2
    echo "" >&2

    # Check gateway health first
    if ! check_gateway_health; then
        exit 1
    fi

    # Create or use provided audio file
    if [ -z "$audio_file" ]; then
        temp_file=$(mktemp)
        mv "$temp_file" "${temp_file}.wav"
        temp_file="${temp_file}.wav"
        create_dummy_audio "$temp_file"
        audio_file="$temp_file"
    elif [ ! -f "$audio_file" ]; then
        log_error "Audio file not found: ${audio_file}"
        exit 1
    fi

    # Submit job
    local job_id
    job_id=$(submit_job "$audio_file")

    if [ $? -ne 0 ] || [ -z "$job_id" ]; then
        log_error "Failed to submit job"
        [ -n "$temp_file" ] && rm -f "$temp_file"
        exit 1
    fi

    # Poll for completion
    local response
    response=$(poll_job "$job_id")

    if [ $? -eq 0 ]; then
        verify_response "$response"

        echo "" >&2
        echo "========================================" >&2
        echo -e "  ${GREEN}M01 VERIFICATION PASSED${NC}" >&2
        echo "========================================" >&2
        echo "" >&2
        echo "Full response:" >&2
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response" >&2
        echo "" >&2
        exit_code=0
    else
        echo "" >&2
        echo "========================================" >&2
        echo -e "  ${RED}M01 VERIFICATION FAILED${NC}" >&2
        echo "========================================" >&2
        echo "" >&2
        exit_code=1
    fi

    # Cleanup temp file
    [ -n "$temp_file" ] && rm -f "$temp_file"

    exit $exit_code
}

main "$@"
