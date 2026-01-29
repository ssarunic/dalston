# M10: Web Console

| | |
|---|---|
| **Goal** | Visual management and monitoring interface |
| **Duration** | 3-4 days |
| **Dependencies** | M6 complete (real-time working) |
| **Deliverable** | React dashboard for jobs, sessions, system status |

## User Story

> *"As an admin, I can monitor jobs, view transcripts, and check system status in a web UI."*

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DALSTON CONSOLE                              │
├─────────────────────────────────────────────────────────────────────┤
│  Dashboard  │  Batch Jobs  │  Realtime  │  Engines  │  Settings     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │   System     │  │  Batch Queue │  │   Realtime   │              │
│  │   ───────    │  │   ────────   │  │   ────────   │              │
│  │   ● Online   │  │   3 running  │  │   8/16 slots │              │
│  │   GPU: 45%   │  │   12 queued  │  │   2 workers  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                      │
│  Recent Jobs                                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ job_abc123  │ completed │ podcast.mp3    │ 2m ago │ [View] │   │
│  │ job_def456  │ running   │ interview.wav  │ 5m ago │ [View] │   │
│  │ job_ghi789  │ pending   │ meeting.m4a    │ 8m ago │ [View] │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 10.1: Project Setup

```
web/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts          # API client
│   ├── hooks/
│   │   ├── useJobs.ts
│   │   ├── useJob.ts
│   │   ├── useStatus.ts
│   │   └── useRealtime.ts
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── BatchJobs.tsx
│   │   ├── JobDetail.tsx
│   │   ├── RealtimeSessions.tsx
│   │   └── Engines.tsx
│   ├── components/
│   │   ├── Layout.tsx
│   │   ├── Sidebar.tsx
│   │   ├── JobList.tsx
│   │   ├── JobCard.tsx
│   │   ├── DAGViewer.tsx
│   │   ├── TranscriptViewer.tsx
│   │   ├── StatusBadge.tsx
│   │   └── CapacityGauge.tsx
│   └── styles/
│       └── globals.css
└── tailwind.config.js
```

```json
// package.json
{
  "name": "dalston-console",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "@tanstack/react-query": "^5.8.0",
    "lucide-react": "^0.294.0",
    "recharts": "^2.10.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "typescript": "^5.3.0",
    "vite": "^5.0.0",
    "tailwindcss": "^3.3.0"
  }
}
```

---

### 10.2: API Client

```typescript
// src/api/client.ts

const API_BASE = '/api/console';

export async function fetchDashboard() {
  const res = await fetch(`${API_BASE}/dashboard`);
  return res.json();
}

export async function fetchJobs(params?: { limit?: number; status?: string }) {
  const query = new URLSearchParams(params as Record<string, string>);
  const res = await fetch(`${API_BASE}/jobs?${query}`);
  return res.json();
}

export async function fetchJob(jobId: string) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`);
  return res.json();
}

export async function fetchJobTasks(jobId: string) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/tasks`);
  return res.json();
}

export async function fetchRealtimeStatus() {
  const res = await fetch(`${API_BASE}/realtime/status`);
  return res.json();
}

export async function fetchEngines() {
  const res = await fetch(`${API_BASE}/engines`);
  return res.json();
}
```

---

### 10.3: Dashboard Page

```tsx
// src/pages/Dashboard.tsx

import { useQuery } from '@tanstack/react-query';
import { fetchDashboard } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import { CapacityGauge } from '../components/CapacityGauge';
import { JobList } from '../components/JobList';

export function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 5000,
  });

  if (isLoading) return <div>Loading...</div>;

  const { system, batch, realtime } = data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      
      {/* Status Cards */}
      <div className="grid grid-cols-3 gap-6">
        {/* System Status */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">System</h2>
          <div className="flex items-center gap-2 mb-2">
            <StatusBadge status={system.status} />
            <span className="text-gray-600">{system.status}</span>
          </div>
          <div className="text-sm text-gray-500">
            GPU: {system.gpu_memory_used_mb}MB / {system.gpu_memory_total_mb}MB
          </div>
          <div className="mt-2 bg-gray-200 rounded-full h-2">
            <div 
              className="bg-blue-500 h-2 rounded-full"
              style={{ width: `${(system.gpu_memory_used_mb / system.gpu_memory_total_mb) * 100}%` }}
            />
          </div>
        </div>

        {/* Batch Queue */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Batch Jobs</h2>
          <div className="text-4xl font-bold text-blue-600">{batch.active_jobs}</div>
          <div className="text-sm text-gray-500">running</div>
          <div className="mt-4 text-sm">
            <span className="text-yellow-600 font-medium">{batch.queue_depth}</span> queued
          </div>
        </div>

        {/* Realtime Capacity */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Realtime</h2>
          <CapacityGauge 
            used={realtime.active_sessions} 
            total={realtime.total_capacity} 
          />
          <div className="mt-2 text-sm text-gray-500">
            {realtime.active_sessions}/{realtime.total_capacity} sessions
          </div>
          <div className="text-sm text-gray-500">
            {realtime.worker_count} workers online
          </div>
        </div>
      </div>

      {/* Recent Jobs */}
      <div className="bg-white rounded-lg shadow">
        <div className="px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">Recent Jobs</h2>
        </div>
        <JobList jobs={batch.recent_jobs} compact />
      </div>
    </div>
  );
}
```

---

### 10.4: Job Detail with DAG

```tsx
// src/pages/JobDetail.tsx

import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchJob, fetchJobTasks } from '../api/client';
import { DAGViewer } from '../components/DAGViewer';
import { TranscriptViewer } from '../components/TranscriptViewer';

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  
  const { data: job } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId!),
    refetchInterval: job?.status === 'running' ? 2000 : false,
  });
  
  const { data: tasks } = useQuery({
    queryKey: ['job-tasks', jobId],
    queryFn: () => fetchJobTasks(jobId!),
    refetchInterval: job?.status === 'running' ? 2000 : false,
  });

  if (!job) return <div>Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{jobId}</h1>
          <p className="text-gray-500">{job.filename}</p>
        </div>
        <StatusBadge status={job.status} size="lg" />
      </div>

      {/* Pipeline DAG */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Pipeline Progress</h2>
        <DAGViewer tasks={tasks?.tasks || []} />
      </div>

      {/* Metadata */}
      <div className="grid grid-cols-4 gap-4">
        <MetadataCard label="Duration" value={formatDuration(job.audio_duration)} />
        <MetadataCard label="Language" value={job.language || 'auto'} />
        <MetadataCard label="Speakers" value={job.speaker_count || '-'} />
        <MetadataCard label="Created" value={formatRelativeTime(job.created_at)} />
      </div>

      {/* Transcript */}
      {job.status === 'completed' && (
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b flex justify-between items-center">
            <h2 className="text-lg font-semibold">Transcript</h2>
            <div className="flex gap-2">
              <ExportButton jobId={jobId!} format="srt" />
              <ExportButton jobId={jobId!} format="vtt" />
              <ExportButton jobId={jobId!} format="txt" />
            </div>
          </div>
          <TranscriptViewer 
            transcript={job.transcript}
            showSpeakers={true}
            showTimestamps={true}
          />
        </div>
      )}
    </div>
  );
}
```

---

### 10.5: DAG Viewer Component

```tsx
// src/components/DAGViewer.tsx

interface Task {
  id: string;
  stage: string;
  engine_id: string;
  status: 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped';
  dependencies: string[];
  started_at?: string;
  completed_at?: string;
}

export function DAGViewer({ tasks }: { tasks: Task[] }) {
  // Group tasks by their stage depth
  const stages = organizeIntoStages(tasks);
  
  return (
    <div className="flex items-center gap-4 overflow-x-auto py-4">
      {stages.map((stage, i) => (
        <div key={i} className="flex flex-col gap-2">
          {stage.map(task => (
            <TaskNode key={task.id} task={task} />
          ))}
          {i < stages.length - 1 && (
            <div className="flex items-center justify-center">
              <ArrowRight className="text-gray-400" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function TaskNode({ task }: { task: Task }) {
  const statusColors = {
    pending: 'bg-gray-100 border-gray-300',
    ready: 'bg-yellow-50 border-yellow-300',
    running: 'bg-blue-50 border-blue-300 animate-pulse',
    completed: 'bg-green-50 border-green-300',
    failed: 'bg-red-50 border-red-300',
    skipped: 'bg-gray-50 border-gray-200',
  };
  
  return (
    <div className={`px-4 py-2 rounded-lg border-2 ${statusColors[task.status]}`}>
      <div className="font-medium text-sm">{task.stage}</div>
      <div className="text-xs text-gray-500">{task.engine_id}</div>
      {task.status === 'running' && (
        <div className="text-xs text-blue-600 mt-1">Processing...</div>
      )}
    </div>
  );
}
```

---

### 10.6: Console API Endpoints

```python
# gateway/api/console.py

from fastapi import APIRouter

router = APIRouter(prefix="/api/console", tags=["Console"])

@router.get("/dashboard")
async def get_dashboard():
    """Aggregate dashboard data."""
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

@router.get("/jobs")
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
):
    """List jobs with pagination."""
    return await jobs_service.list_jobs(
        limit=limit,
        offset=offset,
        status=status
    )

@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: str):
    """Get job with transcript."""
    job = await jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@router.get("/jobs/{job_id}/tasks")
async def get_job_tasks(job_id: str):
    """Get tasks for a job."""
    tasks = await jobs_service.get_job_tasks(job_id)
    return {"tasks": tasks}

@router.get("/realtime/status")
async def get_realtime_status():
    """Get realtime system status."""
    workers = await session_router.list_workers()
    sessions = await session_router.list_active_sessions()
    
    total_capacity = sum(w.capacity for w in workers)
    active_sessions = len(sessions)
    
    return {
        "worker_count": len(workers),
        "total_capacity": total_capacity,
        "active_sessions": active_sessions,
        "workers": workers,
        "sessions": sessions,
    }

@router.get("/realtime/sessions")
async def list_sessions(active_only: bool = True):
    """List realtime sessions."""
    return await session_router.list_sessions(active_only=active_only)

@router.get("/engines")
async def list_engines():
    """List all registered engines."""
    batch = await get_batch_engines()
    realtime = await session_router.list_workers()
    
    return {
        "batch": batch,
        "realtime": realtime,
    }
```

---

### 10.7: Serve React Build

```python
# gateway/main.py

from fastapi.staticfiles import StaticFiles

# Mount console routes
app.include_router(console_router)

# Serve React build (after API routes)
app.mount("/console", StaticFiles(directory="web/dist", html=True), name="console")
```

---

### 10.8: Docker Integration

```dockerfile
# docker/Dockerfile.gateway

# Build React app
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Build Python app
FROM python:3.11-slim
WORKDIR /app

# Copy React build
COPY --from=web-builder /web/dist ./web/dist

# Copy Python code
COPY dalston/gateway ./dalston/gateway
COPY dalston/common ./dalston/common

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn redis

CMD ["uvicorn", "dalston.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Verification

```bash
# Build and start
docker compose up -d --build

# Open console
open http://localhost:8000/console

# Verify API
curl http://localhost:8000/api/console/dashboard
curl http://localhost:8000/api/console/jobs
curl http://localhost:8000/api/console/realtime/status
```

---

## Features Implemented

| Page | Features |
|------|----------|
| **Dashboard** | System status, GPU usage, batch queue depth, realtime capacity, recent jobs |
| **Batch Jobs** | Job list with filters, status badges, pagination |
| **Job Detail** | DAG visualization, transcript viewer, export buttons |
| **Realtime** | Worker list, active sessions, capacity gauge |
| **Engines** | Batch engine status, realtime worker health |

---

## Checkpoint

✓ **React app** with Vite + TypeScript + Tailwind  
✓ **Dashboard** with system overview  
✓ **Job list** with filtering and pagination  
✓ **Job detail** with DAG visualization  
✓ **Transcript viewer** with speakers and timestamps  
✓ **Realtime monitoring** for workers and sessions  
✓ **API endpoints** for all console data  

---

## 🎉 Implementation Complete!

You now have a fully functional transcription server with:
- Batch transcription with speaker diarization
- Real-time streaming with WebSocket
- ElevenLabs API compatibility
- Emotion detection and audio events
- LLM-powered cleanup
- Web management console
