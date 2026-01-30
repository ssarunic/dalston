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

```text
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

```text
web/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts
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

**Stack:** React 18, Vite 5, TypeScript, Tailwind CSS, React Query, Recharts

---

### 10.2: API Client

**Deliverables:**

- `fetchDashboard()` - System status, batch stats, realtime capacity
- `fetchJobs(params)` - List jobs with pagination
- `fetchJob(jobId)` - Job details with transcript
- `fetchJobTasks(jobId)` - Task DAG for job
- `fetchRealtimeStatus()` - Worker pool status
- `fetchEngines()` - Batch and realtime engines

---

### 10.3: Dashboard Page

**Deliverables:**

- System status card (online/offline, GPU memory usage bar)
- Batch queue card (running count, queue depth)
- Realtime capacity card (sessions/capacity, worker count)
- Recent jobs list (compact view, last 5 jobs)
- Auto-refresh every 5 seconds

---

### 10.4: Job Detail Page

**Deliverables:**

- Job header with status badge
- Pipeline DAG visualization showing task progress
- Metadata cards (duration, language, speakers, created time)
- Transcript viewer with speaker labels and timestamps
- Export buttons (SRT, VTT, TXT)
- Auto-refresh while job is running (2 second interval)

---

### 10.5: DAG Viewer Component

**Deliverables:**

- Organize tasks into stage columns by dependencies
- Task nodes show stage name, engine ID, status
- Status colors: pending (gray), ready (yellow), running (blue+pulse), completed (green), failed (red), skipped (gray)
- Arrow connectors between stages

---

### 10.6: Console API Endpoints

**Deliverables:**

| Endpoint | Description |
|----------|-------------|
| `GET /api/console/dashboard` | Aggregated dashboard data |
| `GET /api/console/jobs` | List jobs with pagination |
| `GET /api/console/jobs/{id}` | Job detail with transcript |
| `GET /api/console/jobs/{id}/tasks` | Task DAG for job |
| `GET /api/console/realtime/status` | Worker pool status |
| `GET /api/console/realtime/sessions` | Active sessions |
| `GET /api/console/engines` | All registered engines |

---

### 10.7: Serve React Build

**Deliverables:**

- Mount console routes in gateway
- Serve React build at `/console` path
- Configure static file serving with HTML fallback for SPA routing

---

### 10.8: Docker Integration

**Deliverables:**

- Multi-stage Dockerfile for gateway
- Stage 1: Build React app with Node
- Stage 2: Python app with React build copied in
- React build copied to `web/dist` in container

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

## Features Summary

| Page | Features |
|------|----------|
| **Dashboard** | System status, GPU usage, batch queue depth, realtime capacity, recent jobs |
| **Batch Jobs** | Job list with filters, status badges, pagination |
| **Job Detail** | DAG visualization, transcript viewer, export buttons |
| **Realtime** | Worker list, active sessions, capacity gauge |
| **Engines** | Batch engine status, realtime worker health |

---

## Implementation Summary

Completed: 2026-01-30

### Stack Used

- React 19 + Vite 7 + TypeScript
- Tailwind CSS v3 with dark theme (CSS variables)
- TanStack Query (React Query) for data fetching
- React Router 7 with dynamic basename for `/console/` path
- shadcn/ui-style components (Card, Badge, Table, Skeleton)
- ky HTTP client for API calls

### Key Files

| Component      | Path                                         |
| -------------- | -------------------------------------------- |
| Main App       | `web/src/App.tsx`                            |
| API Client     | `web/src/api/client.ts`                      |
| Dashboard      | `web/src/pages/Dashboard.tsx`                |
| Job Detail     | `web/src/pages/JobDetail.tsx`                |
| DAG Viewer     | `web/src/components/DAGViewer.tsx`           |
| Console API    | `dalston/gateway/api/console.py`             |
| Static Serving | `dalston/gateway/main.py` (lines 125-163)    |
| Dockerfile     | `docker/Dockerfile.gateway`                  |

### API Endpoints Implemented

| Endpoint                           | Description                                                 |
| ---------------------------------- | ----------------------------------------------------------- |
| `GET /api/console/dashboard`       | Aggregated dashboard (system, batch, realtime, recent jobs) |
| `GET /api/console/jobs/{id}/tasks` | Task DAG for job pipeline visualization                     |
| `GET /api/console/engines`         | Batch engine queues + realtime workers                      |

Reused existing endpoints:

- `GET /v1/audio/transcriptions` - Job list
- `GET /v1/audio/transcriptions/{id}` - Job detail
- `GET /v1/audio/transcriptions/{id}/export/{format}` - Export

### Security Notes

- Path traversal protection added to static file serving using `.resolve()` and `.is_relative_to()`
- Console is served without authentication (internal tool assumption)

---

## Checkpoint

- [x] **React app** with Vite + TypeScript + Tailwind
- [x] **Dashboard** with system overview
- [x] **Job list** with filtering and pagination
- [x] **Job detail** with DAG visualization
- [x] **Transcript viewer** with speakers and timestamps
- [x] **Realtime monitoring** for workers and sessions
- [x] **API endpoints** for all console data

**Next**: [M11: API Authentication](M11-api-authentication.md) — Secure all endpoints
