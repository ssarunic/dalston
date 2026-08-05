# M101: Heartbeats Must Preserve Static Registry Fields

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Stop realtime engines being silently quarantined — and then reaped — when a registry key expires and is re-created by a heartbeat that omits the identifying fields |
| **Duration**       | 1 day                                                        |
| **Dependencies**   | M91 (Queue-Based GPU Autoscaling)                            |
| **Deliverable**    | `UnifiedEngineRegistry.heartbeat()` writes the full static field set, `interfaces` added to both writers, regression tests for expiry-then-heartbeat |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator, I want a realtime worker that misses one heartbeat to keep working, so that a transient blip doesn't remove it from the registry permanently and get its instance terminated as a wedged boot."*

---

## Outcomes

| Scenario | Current | After M101 |
| -------- | ------- | ---------- |
| Realtime key expires, next heartbeat re-creates it | Hash has heartbeat fields only. `engine_id` missing → `_mapping_to_record` returns `None` → worker vanishes from the registry for good | Heartbeat rewrites the identifying fields; the record stays valid |
| Console `/realtime` with a healthy realtime worker | "No real-time workers running", 0/0 capacity | Worker listed, capacity correct |
| Autoscaler fleet snapshot | Counts the worker as `pending`; after `boot_timeout_s` the reaper terminates a healthy instance | Counts it as live; no spurious reap |
| Batch key expires and is re-created | Survives (`engine_id`/`stage` rewritten) but `interfaces` is lost and silently defaults to `["batch"]` | `interfaces` is rewritten explicitly |

---

## Motivation

Registry instance keys carry a ~60 s TTL refreshed by heartbeat. The full record is written **once**, by `register()` at engine start. If the key ever lapses — one missed or slow heartbeat under GPU load is enough — the next heartbeat re-creates it from scratch with only the fields that heartbeat happens to send.

The two writers disagree about which fields those are:

| Writer | Used by | `engine_id` | `stage` | `interfaces` |
| ------ | ------- | ----------- | ------- | ------------ |
| `UnifiedRegistryWriter.heartbeat` (sync, `registry.py:637`) | batch engines | ✅ | ✅ | ❌ |
| `UnifiedEngineRegistry.heartbeat` (async, `registry.py:401`) | realtime engines | ❌ | ❌ | ❌ |

The sync writer documents exactly why it rewrites them:

> *engine_id, stage, and node identity fields are written on every heartbeat so that if the Redis key expires and is re-created by the heartbeat, the critical static fields are always present (preventing silent quarantine by `_mapping_to_record`).*

The async writer never received that fix, and its docstring is actively misleading — it claims "node identity fields are static but included so re-created keys survive expiry", but includes only `hostname`, `node_id` and `deploy_env`, none of which `_mapping_to_record` requires.

### Observed in production, 2026-08-05

Worker `i-0c3e9ba6df4ba6fd3`, engine `nemo-rt-i-0c3e9ba6df`:

```
nemo-rt hash: active_realtime deploy_env gpu_memory_used hostname
              last_heartbeat models_loaded node_id status
nemo    hash: active_batch deploy_env engine_id gpu_memory_used hostname
              last_heartbeat node_id stage status
```

`instance`, `engine_id`, `stage`, `interfaces`, `endpoint` and `capacity` were all gone from the realtime record. Consequences, in order:

1. `_mapping_to_record` logged `unified_registry_missing_engine_id` and returned `None`, so `get_all()` returned 3 records instead of 4.
2. `SessionCoordinator.list_workers()` — which filters on `supports_interface("realtime")` — returned 0. The console reported "no workers registered" and `get_capacity()` returned all zeros.
3. `console.py` wraps that call in a bare `except Exception: pass`, so nothing surfaced in logs either.
4. The autoscaler's fleet snapshot counted the instance as **pending** rather than live. At 63 minutes old it passed `boot_timeout_s: 1800` and the reaper terminated it: `"applied": "reaped stuck pending: ['i-0c3e9ba6df4ba6fd3']"`.

So the bug does not merely hide a worker — it destroys the instance, and the replacement inherits the same fault as soon as *its* key lapses. It is self-perpetuating.

The heartbeat keeps refreshing the TTL on the truncated key, so the record never recovers on its own. Only an engine restart (a fresh `register()`) restores it.

### Why `interfaces` matters too

Neither writer sends `interfaces`. Batch survives only because `_mapping_to_record` defaults a missing value to `["batch"]` — so a re-created **realtime** key would be mis-typed as batch even with `engine_id` restored, and would still be invisible to `list_workers()`. Restoring `engine_id` alone is not sufficient.

---

## Architecture

```
register()            heartbeat()                    _mapping_to_record()
─────────             ───────────                    ────────────────────
writes FULL     ──▶   TTL 60s          ──▶  key      requires engine_id
record                refreshed             lives    (+ interfaces to type it)

                      ✗ key lapses
                      ──────────────
                      next heartbeat re-creates the key
                      with ONLY heartbeat fields
                              │
                              ▼
                      engine_id absent ──▶ record dropped ──▶ worker invisible
                                                          └─▶ counted "pending"
                                                          └─▶ reaped at boot_timeout
```

---

## Steps

### 101.1: Async heartbeat writes the static field set

**Files modified:**

- `dalston/common/registry.py` — `UnifiedEngineRegistry.heartbeat()`

**Deliverables:**

Add the identifying parameters the sync writer already accepts, plus `interfaces`, `endpoint` and `capacity` — realtime allocation needs the last two and they are equally lost on re-creation:

```python
async def heartbeat(
    self,
    instance: str,
    *,
    status: str | None = None,
    ...
    engine_id: str | None = None,
    stage: str | None = None,
    interfaces: list[str] | None = None,
    endpoint: str | None = None,
    capacity: int | None = None,
    ...
) -> None:
```

Also write `instance` itself — it is part of the full record and absent from the observed truncated hash.

Correct the docstring, which currently claims re-created keys survive expiry when they do not.

---

### 101.2: `interfaces` on the sync writer

**Files modified:**

- `dalston/common/registry.py` — `UnifiedRegistryWriter.heartbeat()`

**Deliverables:**

Accept and write `interfaces`. Batch currently survives on `_mapping_to_record`'s `["batch"]` default, which is luck rather than design — a unified engine advertising `["batch", "realtime"]` would silently lose its realtime half on re-creation.

---

### 101.3: Callers pass the static fields

**Files modified:**

- `dalston/realtime_sdk/base.py` — `_heartbeat_loop`
- `dalston/engine_sdk/runner.py` — heartbeat call site

**Deliverables:**

Both runners already hold their `EngineRecord`; pass the identifying fields on every heartbeat. Prefer reading them from the record built at registration over re-deriving, so the two can never disagree.

---

### 101.4: Stop swallowing the failure

**Files modified:**

- `dalston/gateway/api/console.py` — the `except Exception: pass` around `list_workers()`

**Deliverables:**

The quarantine was invisible partly because this handler discards the exception and returns an empty list, which renders identically to "no workers are running". Log at warning with the exception, and keep returning a degraded response rather than failing the page.

---

### 101.5: Tests

**Files modified:**

- `tests/unit/test_registry_heartbeat.py` *(new)*

**Deliverables:**

- Expiry simulation: `register()`, delete the key, `heartbeat()`, then assert `get_all()` still yields a valid record with the right `interfaces` — the exact production sequence, for both writers
- A realtime record round-trips as realtime (not defaulted to batch) after re-creation
- `endpoint` and `capacity` survive re-creation, so allocation still works
- Parity test asserting the sync and async heartbeat signatures accept the same field set, so they cannot drift apart again

---

## Non-Goals

- **Removing the TTL/heartbeat design** — expiry-driven liveness is fine; the bug is that re-creation loses identity.
- **Changing `boot_timeout_s` or the reaper** — the reaper behaved correctly given the fleet snapshot it was handed. Fix the snapshot's input, not the reaper.
- **Merging the sync and async writers** — worthwhile, but a larger refactor; this milestone only makes them agree on fields.

---

## Verification

```bash
# Reproduce the production failure directly against a live worker:
# delete the key, let one heartbeat re-create it, confirm the record survives.
docker exec dalston-redis-1 redis-cli DEL dalston:engine:instance:nemo-rt-<id>
sleep 20
docker exec dalston-redis-1 redis-cli HKEYS dalston:engine:instance:nemo-rt-<id>
# want engine_id, stage, interfaces, endpoint, capacity present

# The record must still resolve, and the worker must still be listed.
docker exec -i dalston-gateway-1 python3 -c "
import asyncio, os
from dalston.orchestrator.session_coordinator import SessionCoordinator
async def m():
    sc = SessionCoordinator(redis_url=os.environ['REDIS_URL']); await sc.start()
    print('realtime workers:', len(await sc.list_workers()))
    print('capacity:', await sc.get_capacity())
asyncio.run(m())"
```

Console check: `/console/realtime` shows the worker and non-zero capacity after the key has been deleted and re-created.

---

## Checkpoint

- [ ] Async heartbeat accepts and writes `instance`, `engine_id`, `stage`, `interfaces`, `endpoint`, `capacity`
- [ ] Sync heartbeat accepts and writes `interfaces`
- [ ] Both runners pass the static fields on every heartbeat
- [ ] Deleting a live realtime key and waiting one heartbeat leaves a valid, realtime-typed record
- [ ] `console.py` logs rather than silently swallowing a `list_workers()` failure
- [ ] Signature-parity test prevents the two writers drifting again
