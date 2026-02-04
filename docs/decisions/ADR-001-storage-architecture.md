# ADR-001: Storage Architecture

## Status

Accepted

## Context

Dalston needs to store several categories of data with different characteristics:

- **Job/Task state**: Must survive restarts, needs querying (list jobs by status, etc.)
- **Work queues**: High throughput, ordering guarantees, blocking dequeue
- **Session state**: Ephemeral, needs TTL, real-time updates
- **Audio files and transcripts**: Large blobs, potentially gigabytes per job
- **Rate limit counters**: High-frequency updates, auto-expiring

A single storage system cannot optimally serve all these needs.

## Options Considered

### 1. PostgreSQL Only

Store everything in PostgreSQL, including queues (using `SKIP LOCKED`) and blobs (using `bytea` or `TOAST`).

**Pros:**

- Single system to operate
- Strong consistency guarantees
- Mature tooling

**Cons:**

- Poor fit for queue workloads (polling overhead, lock contention)
- Storing large blobs in database is inefficient and complicates backups
- No native pub/sub for real-time events
- TTL-based expiry requires application logic

### 2. Redis Only

Store everything in Redis, using different data structures for different needs.

**Pros:**

- Excellent for queues (BRPOP), pub/sub, TTL
- Very fast for all operations
- Single system

**Cons:**

- No durable storage (persistence options have tradeoffs)
- Limited query capabilities (no "find all jobs where status=X")
- Memory-bound (expensive for large datasets)
- Large blob storage impractical

### 3. PostgreSQL + Redis + S3 (Chosen)

Use each system for what it does best:

- **PostgreSQL**: Persistent business data (jobs, tasks, API keys, tenants)
- **Redis**: Ephemeral data (queues, session state, rate limits, pub/sub)
- **S3**: Artifact storage (audio, transcripts, exports)

**Pros:**

- Each system used for its strengths
- Clear separation of concerns
- Scales independently per tier
- Cloud-native (managed services available)

**Cons:**

- Three systems to operate
- Data consistency across systems requires care
- More complex deployment

## Decision

Adopt the three-tier storage architecture:

| Data Type | Storage | Rationale |
| --- | --- | --- |
| Jobs, Tasks, API Keys, Tenants | PostgreSQL | Queryable, durable, relational |
| Work queues | Redis Lists | BRPOP, high throughput |
| Session state | Redis Hashes + TTL | Ephemeral, auto-expiring |
| Rate limits | Redis Strings + TTL | High-frequency counters |
| Events | Redis Pub/Sub | Real-time notifications |
| Audio, Transcripts, Exports | S3 | Scalable blob storage |

### Key Design Rules

1. **PostgreSQL is source of truth** for business entities. Redis caches/indexes but doesn't own.
2. **Redis data is ephemeral**. System must recover if Redis is wiped.
3. **S3 URIs stored in PostgreSQL**. Database references artifacts, doesn't store them.
4. **No shared filesystem**. Workers communicate via S3, enabling horizontal scaling.

## Consequences

### Easier

- Horizontal scaling (stateless workers, S3 for shared storage)
- Queue operations (Redis BRPOP vs. PostgreSQL polling)
- Large file handling (S3 handles multi-GB files naturally)
- Real-time features (Redis pub/sub for events)

### Harder

- Local development (need all three systems running)
- Debugging data flow (data spans multiple systems)
- Ensuring consistency (job in PostgreSQL must match tasks, artifacts in S3)
- Recovery scenarios (must consider partial failures across systems)

### Mitigations

- Docker Compose provides easy local setup with all services
- Structured logging with correlation IDs across systems
- Idempotent operations and eventual consistency where appropriate
- Health checks verify connectivity to all storage systems
