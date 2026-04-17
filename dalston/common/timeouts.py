"""Timeout and interval constants.

Centralizes timeout values used across the Dalston codebase to ensure
consistency and make configuration changes easier.
"""

# =============================================================================
# Sync Operation Timeouts
# =============================================================================
# Used for blocking API calls that wait for job completion

SYNC_OPERATION_TIMEOUT_SECONDS = 300  # 5 minutes - max wait for sync transcription
SYNC_POLL_INTERVAL_SECONDS = 1.0  # interval between DB polls during sync wait

# =============================================================================
# WebSocket Connection Timeouts
# =============================================================================
# Used for WebSocket connections between gateway and realtime workers

WS_OPEN_TIMEOUT = 10  # seconds to establish connection
WS_CLOSE_TIMEOUT = 5  # seconds to close connection gracefully
WS_PING_INTERVAL = 20  # seconds between ping frames
WS_PING_TIMEOUT = 20  # seconds to wait for pong response

# =============================================================================
# S3 / Storage Timeouts
# =============================================================================

S3_PRESIGNED_URL_EXPIRY_SECONDS = 3600  # 1 hour - presigned download URLs

# =============================================================================
# Session Management
# =============================================================================

REALTIME_SESSION_TTL_SECONDS = 300  # 5 minutes - session key TTL in Redis

# =============================================================================
# Retry Delays
# =============================================================================

REDIS_RECONNECT_DELAY_SECONDS = 5  # wait before reconnecting to Redis
ERROR_RETRY_DELAY_SECONDS = 1  # wait before retrying after generic errors

# =============================================================================
# Task Processing Timeouts
# =============================================================================
# Fallback used when the scheduler can't compute a duration-aware timeout
# (audio_duration unknown) or when the engine/reconciler recovers a task
# whose original timeout_at metadata isn't available. 1 hour covers
# long-audio diarize/transcribe runs including cold-start model downloads.

TASK_UNKNOWN_DURATION_TIMEOUT_S = 3600
