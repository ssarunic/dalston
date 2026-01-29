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

## Checkpoint

- [ ] **React app** with Vite + TypeScript + Tailwind
- [ ] **Dashboard** with system overview
- [ ] **Job list** with filtering and pagination
- [ ] **Job detail** with DAG visualization
- [ ] **Transcript viewer** with speakers and timestamps
- [ ] **Realtime monitoring** for workers and sessions
- [ ] **API endpoints** for all console data

**Next**: [M11: API Authentication](M11-api-authentication.md) — Secure all endpoints
