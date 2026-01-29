# Console API Patterns

## Dashboard Aggregation

The dashboard endpoint aggregates data from multiple sources into a single response:

```python
@router.get("/api/console/dashboard")
async def get_dashboard():
    """Aggregate dashboard data from multiple sources."""
    return {
        "system": {
            "status": "healthy",
            "gpu_memory_used_mb": await get_gpu_memory_used(),
            "gpu_memory_total_mb": await get_gpu_memory_total(),
        },
        "batch": {
            "active_jobs": await count_jobs_by_status(["running"]),
            "queue_depth": await get_total_queue_depth(),
            "recent_jobs": await get_recent_jobs(limit=5),
        },
        "realtime": await get_realtime_status(),
    }
```

---

## Job Listing with Tenant Scope

All console endpoints must respect tenant isolation:

```python
@router.get("/api/console/jobs")
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    api_key: APIKey = Depends(require_scope("jobs:read")),
):
    """List jobs for current tenant only."""
    return await jobs_service.list_jobs(
        tenant_id=api_key.tenant_id,  # Always scope to tenant
        limit=limit,
        offset=offset,
        status=status
    )
```

---

## Realtime Status Aggregation

```python
@router.get("/api/console/realtime/status")
async def get_realtime_status():
    """Aggregate realtime worker pool status."""
    workers = await session_router.list_workers()
    sessions = await session_router.list_active_sessions()

    total_capacity = sum(w.capacity for w in workers)
    active_sessions = len(sessions)

    return {
        "worker_count": len(workers),
        "total_capacity": total_capacity,
        "active_sessions": active_sessions,
        "utilization": active_sessions / total_capacity if total_capacity > 0 else 0,
        "workers": [
            {
                "id": w.id,
                "endpoint": w.endpoint,
                "capacity": w.capacity,
                "active": w.active_sessions,
                "models": w.models,
                "status": w.status,
                "last_heartbeat": w.last_heartbeat,
            }
            for w in workers
        ],
    }
```

---

## Task DAG for Job Detail

```python
@router.get("/api/console/jobs/{job_id}/tasks")
async def get_job_tasks(
    job_id: str,
    api_key: APIKey = Depends(require_scope("jobs:read")),
):
    """Get task DAG for a job."""
    # Verify job belongs to tenant
    job = await jobs_service.get_job(job_id, api_key.tenant_id)
    if not job:
        raise HTTPException(404, "Job not found")

    tasks = await jobs_service.get_job_tasks(job_id)

    return {
        "job_id": job_id,
        "tasks": [
            {
                "id": t.id,
                "stage": t.stage,
                "engine_id": t.engine_id,
                "status": t.status,
                "dependencies": t.dependencies,
                "required": t.required,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "error": t.error if t.status == "failed" else None,
            }
            for t in tasks
        ]
    }
```

---

## React Query Integration

Frontend uses React Query for data fetching with auto-refresh:

```typescript
// Dashboard with 5-second refresh
const { data, isLoading } = useQuery({
  queryKey: ['dashboard'],
  queryFn: fetchDashboard,
  refetchInterval: 5000,
});

// Job detail with conditional refresh (only while running)
const { data: job } = useQuery({
  queryKey: ['job', jobId],
  queryFn: () => fetchJob(jobId),
  refetchInterval: (data) => data?.status === 'running' ? 2000 : false,
});
```

---

## DAG Visualization Data Structure

The frontend organizes tasks into stages for visualization:

```typescript
interface Task {
  id: string;
  stage: string;
  engine_id: string;
  status: 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped';
  dependencies: string[];
}

function organizeIntoStages(tasks: Task[]): Task[][] {
  // Group by dependency depth
  const stages: Task[][] = [];
  const placed = new Set<string>();

  while (placed.size < tasks.length) {
    const stage = tasks.filter(t =>
      !placed.has(t.id) &&
      t.dependencies.every(d => placed.has(d))
    );

    if (stage.length === 0) break; // Prevent infinite loop

    stages.push(stage);
    stage.forEach(t => placed.add(t.id));
  }

  return stages;
}
```

---

## Status Badge Colors

Consistent status colors across the UI:

```typescript
const statusColors = {
  pending: 'bg-gray-100 border-gray-300 text-gray-600',
  ready: 'bg-yellow-50 border-yellow-300 text-yellow-700',
  running: 'bg-blue-50 border-blue-300 text-blue-700 animate-pulse',
  completed: 'bg-green-50 border-green-300 text-green-700',
  failed: 'bg-red-50 border-red-300 text-red-700',
  skipped: 'bg-gray-50 border-gray-200 text-gray-400',
};
```

---

## Serving React Build

```python
from fastapi.staticfiles import StaticFiles

# Mount API routes first
app.include_router(console_router)

# Serve React build last (catch-all for SPA routing)
app.mount("/console", StaticFiles(directory="web/dist", html=True), name="console")
```

The `html=True` parameter enables SPA routing by serving `index.html` for unmatched routes.
