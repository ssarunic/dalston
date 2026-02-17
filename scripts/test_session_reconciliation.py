#!/usr/bin/env python3
"""Test script for session reconciliation.

This script simulates what happens when a Gateway crashes during an active session:
1. Creates orphaned session state in Redis (session in ACTIVE_SESSIONS_KEY but key expired)
2. Runs the reconciliation logic
3. Verifies cleanup

Usage:
    python scripts/test_session_reconciliation.py
"""

import asyncio
import sys
from datetime import UTC, datetime

import redis.asyncio as redis

# Redis keys (from session_router/registry.py)
WORKER_SET_KEY = "dalston:realtime:workers"
WORKER_KEY_PREFIX = "dalston:realtime:worker:"
WORKER_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
ACTIVE_SESSIONS_KEY = "dalston:realtime:sessions:active"


async def main():
    print("=" * 60)
    print("Session Reconciliation Test")
    print("=" * 60)

    # Connect to Redis
    redis_url = "redis://localhost:6379"
    r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)

    try:
        await r.ping()
        print(f"[OK] Connected to Redis at {redis_url}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to Redis: {e}")
        sys.exit(1)

    # Step 1: Check current state
    print("\n--- Step 1: Check current state ---")
    active_sessions = await r.smembers(ACTIVE_SESSIONS_KEY)
    workers = await r.smembers(WORKER_SET_KEY)
    print(f"Active sessions in index: {len(active_sessions)}")
    print(f"Registered workers: {len(workers)}")

    for worker_id in workers:
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        active = await r.hget(worker_key, "active_sessions")
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
        session_set = await r.smembers(sessions_key)
        print(
            f"  Worker {worker_id}: active_sessions={active}, session_set={len(session_set)}"
        )

    # Step 2: Simulate orphaned session (Gateway crash scenario)
    print("\n--- Step 2: Simulate Gateway crash (create orphaned session) ---")
    orphan_session_id = "sess_test_orphan_123"

    # Pick a worker to "assign" the orphaned session to
    if not workers:
        print("[WARN] No workers registered, creating a fake worker for test")
        test_worker_id = "test-worker-orphan"
        await r.sadd(WORKER_SET_KEY, test_worker_id)
        worker_key = f"{WORKER_KEY_PREFIX}{test_worker_id}"
        await r.hset(
            worker_key,
            mapping={
                "endpoint": "ws://localhost:9999",
                "status": "ready",
                "capacity": "4",
                "active_sessions": "0",
                "models_loaded": "[]",
                "languages_supported": "[]",
                "engine": "test",
                "last_heartbeat": datetime.now(UTC).isoformat(),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        workers = {test_worker_id}

    worker_id = list(workers)[0]
    print(f"Using worker: {worker_id}")

    # Add orphaned session to active sessions index
    await r.sadd(ACTIVE_SESSIONS_KEY, orphan_session_id)
    print(f"[DONE] Added {orphan_session_id} to ACTIVE_SESSIONS_KEY")

    # Add to worker's session set
    sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
    await r.sadd(sessions_key, orphan_session_id)
    print(f"[DONE] Added {orphan_session_id} to worker's session set")

    # Increment worker's active_sessions counter
    worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
    old_count = int(await r.hget(worker_key, "active_sessions") or 0)
    await r.hincrby(worker_key, "active_sessions", 1)
    new_count = int(await r.hget(worker_key, "active_sessions"))
    print(f"[DONE] Incremented worker active_sessions: {old_count} -> {new_count}")

    # DO NOT create the session key - this simulates it having expired
    session_key = f"{SESSION_KEY_PREFIX}{orphan_session_id}"
    exists = await r.exists(session_key)
    print(f"[INFO] Session key exists: {exists} (should be False to simulate expiry)")

    # Step 3: Verify orphaned state
    print("\n--- Step 3: Verify orphaned state ---")
    in_active = await r.sismember(ACTIVE_SESSIONS_KEY, orphan_session_id)
    in_worker_set = await r.sismember(sessions_key, orphan_session_id)
    current_count = int(await r.hget(worker_key, "active_sessions") or 0)
    print(f"Session in ACTIVE_SESSIONS_KEY: {in_active}")
    print(f"Session in worker's session set: {in_worker_set}")
    print(f"Worker active_sessions counter: {current_count}")

    # Step 4: Run reconciliation
    print("\n--- Step 4: Run reconciliation ---")
    from dalston.session_router.health import HealthMonitor
    from dalston.session_router.registry import WorkerRegistry

    registry = WorkerRegistry(r)
    monitor = HealthMonitor(r, registry)

    cleaned = await monitor.reconcile_orphaned_sessions()
    print(f"[DONE] Reconciliation cleaned {cleaned} orphaned session(s)")

    # Step 5: Verify cleanup
    print("\n--- Step 5: Verify cleanup ---")
    in_active_after = await r.sismember(ACTIVE_SESSIONS_KEY, orphan_session_id)
    in_worker_set_after = await r.sismember(sessions_key, orphan_session_id)
    count_after = int(await r.hget(worker_key, "active_sessions") or 0)

    print(f"Session in ACTIVE_SESSIONS_KEY: {in_active_after} (expected: False)")
    print(f"Session in worker's session set: {in_worker_set_after} (expected: False)")
    print(f"Worker active_sessions counter: {count_after} (expected: {old_count})")

    # Validate results
    success = True
    if in_active_after:
        print("[FAIL] Session still in ACTIVE_SESSIONS_KEY!")
        success = False
    if in_worker_set_after:
        print("[FAIL] Session still in worker's session set!")
        success = False
    if count_after != old_count:
        print(f"[FAIL] Counter not restored! Expected {old_count}, got {count_after}")
        success = False

    print("\n" + "=" * 60)
    if success:
        print("TEST PASSED: Orphaned session was properly cleaned up!")
    else:
        print("TEST FAILED: Some cleanup did not happen correctly.")
    print("=" * 60)

    # Cleanup test worker if we created one
    if "test-worker-orphan" in workers:
        await r.srem(WORKER_SET_KEY, "test-worker-orphan")
        await r.delete(f"{WORKER_KEY_PREFIX}test-worker-orphan")
        await r.delete(f"{WORKER_KEY_PREFIX}test-worker-orphan{WORKER_SESSIONS_SUFFIX}")

    await r.close()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
