#!/usr/bin/env python3
"""End-to-end test for session reconciliation with real WebSocket session.

This script:
1. Starts a realtime transcription session with generated audio
2. Simulates a Gateway crash by deleting the session key mid-session
3. Verifies the HealthMonitor reconciliation cleans up orphaned state

Usage:
    python scripts/test_session_reconciliation_e2e.py
"""

import asyncio
import json
import math
import os
import struct
import sys

import redis.asyncio as redis
import websockets

# Redis keys (from session_router/registry.py)
WORKER_SET_KEY = "dalston:realtime:workers"
WORKER_KEY_PREFIX = "dalston:realtime:worker:"
WORKER_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
ACTIVE_SESSIONS_KEY = "dalston:realtime:sessions:active"


def generate_sine_wave_chunk(
    frequency: float = 440.0,
    sample_rate: int = 16000,
    duration_ms: int = 100,
) -> bytes:
    """Generate a sine wave audio chunk as PCM S16LE."""
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * t))
        samples.append(sample)
    return struct.pack(f"<{len(samples)}h", *samples)


async def start_session_and_simulate_crash(
    ws_url: str,
    api_key: str,
    redis_client: redis.Redis,
) -> tuple[str, str]:
    """Start a session and return session_id and worker_id after simulating crash."""
    session_id = None
    worker_id = None

    print("\n--- Starting WebSocket session ---")
    async with websockets.connect(ws_url, ping_interval=None) as ws:
        # Start receiving in background
        recv_task = asyncio.create_task(receive_messages(ws))

        # Send some audio to establish session
        print("[INFO] Sending audio chunks...")
        for i in range(10):
            chunk = generate_sine_wave_chunk(frequency=440 + i * 50)
            await ws.send(chunk)
            await asyncio.sleep(0.1)

        # Give server time to process and establish session
        await asyncio.sleep(0.5)

        # Find the session from Redis
        active_sessions = await redis_client.smembers(ACTIVE_SESSIONS_KEY)
        if not active_sessions:
            print("[ERROR] No active sessions found!")
            recv_task.cancel()
            return None, None

        # Get the most recent session
        session_id = list(active_sessions)[0]
        print(f"[INFO] Found session: {session_id}")

        # Get the session data to find worker
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"
        session_data = await redis_client.hgetall(session_key)
        worker_id = session_data.get("worker_id")
        print(f"[INFO] Session assigned to worker: {worker_id}")

        # Verify current state
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        active_count_before = int(
            await redis_client.hget(worker_key, "active_sessions") or 0
        )
        print(f"[INFO] Worker active_sessions before crash: {active_count_before}")

        # SIMULATE CRASH: Delete the session key (as if it expired)
        print("\n--- Simulating Gateway crash (deleting session key) ---")
        await redis_client.delete(session_key)
        print(f"[DONE] Deleted session key: {session_key}")

        # Verify orphaned state exists
        in_active = await redis_client.sismember(ACTIVE_SESSIONS_KEY, session_id)
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
        in_worker = await redis_client.sismember(sessions_key, session_id)
        active_count = int(await redis_client.hget(worker_key, "active_sessions") or 0)

        print(f"[CHECK] Session in ACTIVE_SESSIONS_KEY: {in_active} (should be True)")
        print(f"[CHECK] Session in worker set: {in_worker} (should be True)")
        print(
            f"[CHECK] Worker active_sessions: {active_count} (should be {active_count_before})"
        )

        # Close WebSocket gracefully (but the session key is already gone)
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

        # Send end to cleanly close
        try:
            await ws.send(json.dumps({"type": "end"}))
        except Exception:
            pass

    return session_id, worker_id


async def receive_messages(ws) -> None:
    """Receive WebSocket messages."""
    try:
        async for message in ws:
            if isinstance(message, str):
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "session.begin":
                    print(f"[WS] Session started: {data.get('session_id')}")
                elif msg_type == "transcript.final":
                    text = data.get("text", "")[:50]
                    print(f"[WS] Transcript: {text}")
                elif msg_type == "error":
                    print(f"[WS] Error: {data.get('message')}")
    except asyncio.CancelledError:
        pass
    except websockets.exceptions.ConnectionClosed:
        pass


async def wait_for_reconciliation(
    redis_client: redis.Redis,
    session_id: str,
    worker_id: str,
    timeout: float = 30.0,
) -> bool:
    """Wait for reconciliation to clean up orphaned session."""
    print(f"\n--- Waiting for reconciliation (timeout: {timeout}s) ---")
    print("[INFO] Health monitor runs every 10 seconds...")

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        # Check if session still in active set
        in_active = await redis_client.sismember(ACTIVE_SESSIONS_KEY, session_id)

        if not in_active:
            elapsed = asyncio.get_event_loop().time() - start
            print(f"[OK] Session cleaned from ACTIVE_SESSIONS_KEY after {elapsed:.1f}s")
            return True

        await asyncio.sleep(1)
        sys.stdout.write(".")
        sys.stdout.flush()

    print("\n[TIMEOUT] Session was not cleaned up in time")
    return False


async def main():
    print("=" * 60)
    print("Session Reconciliation E2E Test")
    print("=" * 60)

    # Configuration
    api_key = os.environ.get("DALSTON_API_KEY", "test-key")
    ws_url = f"ws://localhost:8000/v1/audio/transcriptions/stream?api_key={api_key}&model=parakeet&language=auto&sample_rate=16000&encoding=pcm_s16le&enable_vad=false&interim_results=false"
    redis_url = "redis://localhost:6379"

    # Connect to Redis
    r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    try:
        await r.ping()
        print("[OK] Connected to Redis")
    except Exception as e:
        print(f"[ERROR] Cannot connect to Redis: {e}")
        return 1

    # Check for workers
    workers = await r.smembers(WORKER_SET_KEY)
    if not workers:
        print("[ERROR] No realtime workers registered. Start a worker first.")
        return 1
    print(f"[OK] Found {len(workers)} worker(s): {', '.join(workers)}")

    # Get initial worker state
    worker_id = list(workers)[0]
    worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
    initial_count = int(await r.hget(worker_key, "active_sessions") or 0)
    print(f"[INFO] Initial worker active_sessions: {initial_count}")

    try:
        # Start session and simulate crash
        session_id, assigned_worker = await start_session_and_simulate_crash(
            ws_url, api_key, r
        )

        if not session_id:
            print("[ERROR] Failed to establish session")
            return 1

        # Wait for reconciliation
        cleaned = await wait_for_reconciliation(r, session_id, assigned_worker)

        if not cleaned:
            print("\n[FAIL] Reconciliation did not clean up orphaned session!")
            return 1

        # Verify final state
        print("\n--- Verifying final state ---")
        sessions_key = f"{WORKER_KEY_PREFIX}{assigned_worker}{WORKER_SESSIONS_SUFFIX}"
        in_active = await r.sismember(ACTIVE_SESSIONS_KEY, session_id)
        in_worker = await r.sismember(sessions_key, session_id)
        final_count = int(await r.hget(worker_key, "active_sessions") or 0)

        print(f"Session in ACTIVE_SESSIONS_KEY: {in_active} (expected: False)")
        print(f"Session in worker set: {in_worker} (expected: False)")
        print(f"Worker active_sessions: {final_count} (expected: {initial_count})")

        success = not in_active and not in_worker and final_count == initial_count

        print("\n" + "=" * 60)
        if success:
            print("TEST PASSED: Orphaned session was properly cleaned up!")
        else:
            print("TEST FAILED: Some state was not cleaned properly.")
        print("=" * 60)

        return 0 if success else 1

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"[ERROR] WebSocket connection rejected: HTTP {e.status_code}")
        if e.status_code == 4503:
            print("[HINT] No realtime workers available. Check worker is running.")
        return 1
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        await r.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
