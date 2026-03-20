# 12 — Infrastructure

**Route:** `/infrastructure`
**Component:** `src/pages/Infrastructure.tsx`
**Auth required:** Yes

## Purpose

Physical/logical node view of the infrastructure. Shows all compute nodes, their GPU utilization, and the engines running on each node.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Infrastructure                                              │
│  Physical nodes and compute resources                        │
│                                                              │
│  ┌──────────────────┐ ┌──────────────────┐ ┌───────────────┐│
│  │ 🖥 gpu-node-01   │ │ 🖥 gpu-node-02   │ │ 🖥 cpu-node-01││
│  │  [AWS·eu-w-2a·g5]│ │  [AWS·eu-w-2b·g5]│ │   [Local Dev] ││
│  │                  │ │                  │ │  CPU only      ││
│  │  GPU Memory      │ │  GPU Memory      │ │               ││
│  │  12.4/24.0 GB    │ │  8.1/24.0 GB     │ │  ─────────────││
│  │  ██████████░░░   │ │  ████████░░░░░   │ │  prepare      ││
│  │                  │ │                  │ │  audio-prepare ││
│  │  ─────────────── │ │  ─────────────── │ │  ● Idle  0/1  ││
│  │  transcribe      │ │  transcribe      │ │  batch        ││
│  │  faster-whisper   │ │  parakeet-rnnt   │ │               ││
│  │  ● Idle 1/4      │ │  ● Busy 2/4      │ │  ─────────────││
│  │  batch + rt       │ │  batch + rt      │ │  merge        ││
│  │                  │ │                  │ │  final-merger  ││
│  │  ─────────────── │ │  ─────────────── │ │  ● Idle  0/1  ││
│  │  diarize         │ │  align           │ │  batch        ││
│  │  pyannote-diarize│ │  ctc-forced-align│ │               ││
│  │  ● Idle 0/2      │ │  ● Idle 0/2      │ │               ││
│  │  batch            │ │  batch           │ │               ││
│  └──────────────────┘ └──────────────────┘ └───────────────┘│
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Layout

**Grid:** `md:grid-cols-2 lg:grid-cols-3` — one card per node.

**Sort order:** AWS nodes first, then by earliest pipeline stage, then hostname.

## Node Card Elements

| Element | Description |
|---------|-------------|
| Title | Node ID with Server icon |
| Deploy badge | "AWS · eu-west-2a · g5.xlarge" (warning color) or "Local Dev" (secondary color) |
| CPU label | "CPU only" text shown when no GPU |
| GPU Memory bar | Progress bar: used/total GB. Color: green <75%, amber 75-90%, red >90% |
| Engine rows | One per engine on the node, sorted by pipeline stage |

### Engine Row

```
[stage pill]  engine-id  interface-label    ● status  active/capacity
```

| Element | Description |
|---------|-------------|
| Stage pill | Colored badge matching pipeline stage |
| Engine ID | Name of the engine |
| Interface label | "batch", "realtime", or "batch + rt" |
| Status dot | Green (healthy idle), yellow (busy/processing), red (offline/unhealthy) |
| Capacity | "active / capacity" in tabular-nums |

## Behaviour

- Data from `useNodes()` hook — fetches `/console/infrastructure/nodes`.
- Loading state: 3-column skeleton grid.
- Empty state: centered Network icon + "No nodes registered" + hint.
- Error state: red card with error message.
- No interactivity beyond viewing (no links, no actions).
