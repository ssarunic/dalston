# M78: Infrastructure Topology View

|                    |                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------ |
| **Goal**           | Give operators a node-level view of the running infrastructure so they can see which engines are co-located, how many instances exist, and whether they are on local dev or AWS |
| **Duration**       | 3–5 days                                                                             |
| **Dependencies**   | M64 (unified engine registry — complete), M69 (registry migration — complete)        |
| **Deliverable**    | `hostname`, `node_id`, `deploy_env` fields in `EngineRecord`; `/api/console/nodes` endpoint; Infrastructure page in web console showing node cards grouped by host |
| **Status**         | Not Started                                                                          |

## User Story

> *"As an operator, I want to open the Infrastructure page and immediately see that onnx-silero-vad and pyannote are on the same GPU node (or different ones), how many instances of each engine are running, and whether that node is a local dev machine or an AWS EC2 instance — without having to cross-reference hostnames manually."*

---

## Outcomes

| Scenario | Current | After M78 |
| -------- | ------- | --------- |
| Two engines on same GPU box | Cannot tell — only individual engine rows visible on Engines page | Both appear inside the same node card with shared GPU memory bar |
| Three onnx-silero instances across two nodes | Appears as one engine row with capacity=12 | Two node cards: node-1 with 2 instances, node-2 with 1 instance |
| Operator asks "is this local or AWS?" | Must check hostnames or SSH in | Node card shows "AWS · us-east-1b · i-0abc123" or "Local dev" badge |
| GPU node is saturated | Must calculate from individual engine rows | Node card shows aggregate GPU bar in red (e.g. "15.8 / 16 GB") |

---

## Architecture

```
┌─────────────────────── Engine Startup ────────────────────────┐
│  1. socket.gethostname()           → hostname                  │
│  2. IMDSv2 probe (500ms timeout)   → node_id (EC2 instance-id) │
│     or DALSTON_DEPLOY_ENV override → deploy_env                │
│  3. Stored in EngineRecord on registration + heartbeat         │
└───────────────────────────┬───────────────────────────────────┘
                            │ Redis  dalston:engine:instance:{id}
                            ▼
┌─────────────────── /api/console/nodes ────────────────────────┐
│  Groups EngineRecords by (hostname, node_id)                   │
│  Returns NodeRecord[] with aggregated GPU, capacity, engines   │
└───────────────────────────┬───────────────────────────────────┘
                            │ JSON
                            ▼
┌─────────────── Infrastructure Page (web console) ─────────────┐
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  gpu-node-1  AWS · eu-west-2a · i-0abc123   11.2/16 GB │   │
│  │ ─────────────────────────────────────────────────────── │   │
│  │  [transcribe] faster-whisper      ● idle   2/4 cap      │   │
│  │  [diarize]    pyannote            ● idle   1/2 cap      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  gpu-node-2  AWS · eu-west-2a · i-0def456   4.1/16 GB  │   │
│  │ ─────────────────────────────────────────────────────── │   │
│  │  [transcribe] onnx-silero-vad     ● busy   4/4 cap      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────┐                        │
│  │  local-mac  Local dev               │                        │
│  │ ─────────────────────────────────── │                        │
│  │  [prepare]   audio-prepare  ● idle  │                        │
│  │  [merge]     final-merger   ● idle  │                        │
│  └─────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 78.1: Node Identity Detection

**Files modified:**

- `dalston/common/node_identity.py` *(new)*
- `dalston/engine_sdk/runner.py`
- `dalston/realtime_sdk/base.py`

**Deliverables:**

A small, zero-dependency module that collects node metadata at startup and caches the result. Called once by both batch and realtime engine runners before registration.

```python
@dataclass
class NodeIdentity:
    hostname: str        # socket.gethostname()
    node_id: str         # EC2 instance ID, or hostname as fallback
    deploy_env: str      # "aws" | "local"
    region: str | None   # AWS AZ/region, or None on local
    instance_type: str | None  # e.g. "g4dn.xlarge", or None on local

def detect_node_identity() -> NodeIdentity:
    """Detect node identity once at startup. Results are cached module-level."""
    ...
```

Detection logic (in priority order):

1. **`DALSTON_DEPLOY_ENV`** env var — explicit override, values `aws` or `local`
2. **IMDSv2 probe** — PUT `http://169.254.169.254/latest/api/token` with 500ms timeout; if it responds, GET `instance-id`, `instance-type`, `placement/availability-zone`. Sets `deploy_env="aws"`.
3. **Fallback** — `deploy_env="local"`, `node_id=hostname`

The IMDSv2 token PUT uses `X-aws-ec2-metadata-token-ttl-seconds: 21600`. If the PUT times out or refuses connection, we are not on EC2 and fall through immediately (no retry). Total startup overhead on non-EC2: ≤500ms (the timeout).

**Why IMDSv2 over IMDSv1:** IMDSv2 is required on hardened EC2 instances where IMDSv1 is disabled. IMDSv1 still works on most instances, but IMDSv2 is the safe default. Both use the link-local `169.254.169.254` address which is non-routable outside EC2.

---

### 78.2: Add Node Fields to EngineRecord

**Files modified:**

- `dalston/common/registry.py`
- `dalston/engine_sdk/runner.py`
- `dalston/realtime_sdk/base.py`

**Deliverables:**

Four new fields on `EngineRecord`:

```python
@dataclass
class EngineRecord:
    ...
    hostname: str = ""                  # socket.gethostname()
    node_id: str = ""                   # EC2 instance ID or hostname
    deploy_env: str = "local"           # "aws" | "local"
    aws_az: str | None = None           # e.g. "eu-west-2a", None on local
    aws_instance_type: str | None = None  # e.g. "g4dn.xlarge", None on local
```

Fields are populated from `NodeIdentity` during `EngineRunner.__init__()` and `RealtimeEngine.__init__()`, written to Redis on registration and refreshed on every heartbeat (static fields that don't change after startup, so heartbeat can skip them after first write).

`_record_to_mapping` / `_mapping_to_record` updated to serialise/deserialise the new fields. No migration needed — missing fields in existing Redis hashes default gracefully.

---

### 78.3: `/api/console/nodes` Endpoint

**Files modified:**

- `dalston/gateway/console_router.py`
- `dalston/gateway/console_schemas.py`

**Deliverables:**

New read-only endpoint that groups engines by node:

```
GET /api/console/nodes
```

```python
class NodeEngine(BaseModel):
    instance: str
    engine_id: str
    stage: str
    status: str
    interfaces: list[str]
    capacity: int
    active_batch: int
    active_realtime: int
    loaded_model: str | None
    is_healthy: bool

class NodeRecord(BaseModel):
    node_id: str                     # EC2 instance ID or hostname
    hostname: str
    deploy_env: str                  # "aws" | "local"
    aws_az: str | None
    aws_instance_type: str | None
    gpu_memory_used_gb: float        # max across engines (nvidia-smi reading is node-wide)
    gpu_memory_total_gb: float       # max across engines (all share the same GPU)
    engine_count: int
    engines: list[NodeEngine]

class NodesResponse(BaseModel):
    nodes: list[NodeRecord]
```

Grouping logic: engines are grouped by `node_id`. GPU memory is taken from the engine reporting the highest `gpu_memory_total` (all engines on a GPU node share the same GPU, so the total is the same across all; used is summed or taken from the one with the latest heartbeat — either approach is fine for v1).

---

### 78.4: Infrastructure Page (Web Console)

**Files modified:**

- `web/src/pages/Infrastructure.tsx` *(new)*
- `web/src/api/console.ts`
- `web/src/App.tsx`
- `web/src/components/Sidebar.tsx` (or equivalent nav component)

**Deliverables:**

New page at `/infrastructure` showing node cards. Each card:

- **Header**: hostname, `deploy_env` badge (`AWS` in amber, `Local dev` in slate), AZ and instance type (if AWS)
- **GPU bar**: horizontal progress bar showing `gpu_memory_used / gpu_memory_total` in GB; turns amber >75%, red >90%; hidden if `gpu_memory_total == 0`
- **Engine rows**: one row per engine instance — stage pill (colour-coded by stage), engine_id, status dot, capacity fraction

Cards are sorted: AWS nodes first, then local; within group, sorted by hostname.

No diagram library needed — plain CSS flexbox. The layout from the Architecture section above is the target.

Page polls `/api/console/nodes` every 10 seconds (same interval as engine heartbeats).

---

## Non-Goals

- **Interactive topology diagram** (ReactFlow/D3) — Node cards give 90% of the insight. A flow diagram is a separate milestone if it proves valuable.
- **Historical node data** — This is a live view only. Redis TTL means offline engines disappear within 60s.
- **Network topology** (which nodes can reach which) — Not tracked and not needed for the current use case.
- **Docker container ID tracking** — Container IDs are not meaningful to operators; hostname is sufficient for local dev.
- **ECS task ARN or Kubernetes pod ID** — Out of scope for the current AWS deployment model (standalone EC2, not ECS/K8s).

---

## Deployment

No ordering constraints. The new fields on `EngineRecord` have defaults; existing engine instances without the fields will appear on the Infrastructure page grouped under a synthetic `node_id` derived from their hostname (falling back to instance prefix). Engines pick up the new code on their next container restart.

---

## Verification

```bash
make dev

# Check that engines report node fields
docker compose exec redis redis-cli HGETALL dalston:engine:instance:$(
  docker compose exec redis redis-cli SMEMBERS dalston:engine:instances | head -1
)
# Expected: fields "hostname", "node_id", "deploy_env" present with non-empty values

# Fetch the nodes endpoint
curl -s http://localhost:8000/api/console/nodes \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.nodes[] | {node_id, hostname, deploy_env, engine_count}'
# Expected: at least one node with deploy_env="local" and engine_count > 0

# Open Infrastructure page
open http://localhost:5173/infrastructure
# Expected: node cards visible, GPU bar shown for GPU-enabled engines

# Verify AWS detection (on an actual EC2 instance)
curl -s http://localhost:8000/api/console/nodes \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.nodes[] | select(.deploy_env == "aws") | {node_id, aws_az, aws_instance_type}'
# Expected: node_id matches EC2 instance ID, aws_az and aws_instance_type populated
```

---

## Checkpoint

- [ ] `NodeIdentity` module detects AWS via IMDSv2 with ≤500ms timeout on non-EC2
- [ ] `DALSTON_DEPLOY_ENV` env var overrides auto-detection
- [ ] `hostname`, `node_id`, `deploy_env`, `aws_az`, `aws_instance_type` stored in Redis per engine instance
- [ ] `/api/console/nodes` returns engines grouped by node with aggregated GPU stats
- [ ] Infrastructure page shows node cards with GPU bar, engine rows, and deploy_env badge
- [ ] Cards poll every 10s; stale engines (missed heartbeat) show as offline within 60s
- [ ] Local `make dev` stack shows `deploy_env="local"` on all nodes
- [ ] AWS stack shows `deploy_env="aws"` with correct EC2 instance IDs and AZ
