# Realtime session coordination

> Historical name: session router. The current implementation is the
> `SessionCoordinator` in `dalston/orchestrator/session_coordinator.py`, embedded
> in the gateway process.

The coordinator manages realtime worker discovery, least-loaded allocation,
session reservations, keepalive, release, and stale-state reconciliation. It is
not an independently deployed HTTP service.

## Lifecycle

1. A realtime adapter authenticates the WebSocket and resolves its model,
   language, and engine constraints.
2. `SessionCoordinator.acquire_worker()` filters live compatible workers.
3. A Redis-side atomic reservation increments worker use and creates session
   state with a five-minute TTL.
4. The gateway connects to the worker endpoint and refreshes the TTL once per
   minute.
5. On completion or disconnect, the shared proxy releases the reservation.
6. The coordinator health loop marks stale workers offline and repairs orphaned
   reservations after abnormal gateway exits.

Allocation uses available capacity, not round-robin. A worker must advertise
the required interface, engine/model compatibility, language support, and a
free slot. The gateway returns close code `4503` when no worker can be
reserved.

## Redis state

Redis stores live registry records, worker/session indexes, reservation state,
heartbeats, and coordinator events. The exact keys are implementation details
defined in `dalston/common/registry.py`,
`dalston/orchestrator/session_allocator.py`, and
`dalston/orchestrator/session_coordinator.py`; external clients must use the
management API instead of reading keys.

The coordinator does not own durable audio or transcript storage. Persistence
is handled by the gateway's `RealtimeProxy` and
`RealtimeSessionService`.

## Management API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/realtime/status` | Aggregate worker capacity |
| `GET` | `/v1/realtime/workers` | List live workers |
| `GET` | `/v1/realtime/workers/{instance}` | Inspect one worker |
| `GET` | `/v1/realtime/sessions` | List persisted session records |

The first three endpoints describe live coordination. Session-history
endpoints read the database and are not a direct view of Redis reservations.

## Deployment

Start the gateway and Redis; no `session-router` Compose service or
`config/session_router.yaml` is required. Realtime engines register themselves
through the realtime SDK and publish their internal WebSocket endpoint.

When scaling gateways, verify the coordinator's Redis operations and
reconciliation behavior under the intended topology. Do not infer
multi-gateway support solely from the absence of local process state.

## Observability

Coordinator metrics cover registered/healthy workers, total/used/available
capacity, active sessions, allocation outcomes, and stale-worker cleanup.
Inspect `/metrics`, `/v1/realtime/status`, and structured gateway logs together.
