# 03 — Batch Jobs List

**Route:** `/jobs`
**Component:** `src/pages/BatchJobs.tsx`
**Auth required:** Yes

## Purpose

List and manage all batch transcription jobs with filtering, sorting, and bulk actions.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Batch Jobs                              [+ Submit Job]      │
│  Manage transcription jobs                                   │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ All Jobs           [Status ▾] [Sort ▾] [Page size ▾]  │  │
│  │────────────────────────────────────────────────────────│  │
│  │ Name      │Status │Model   │Duration│Segs│Created│Act │  │
│  │───────────┼───────┼────────┼────────┼────┼───────┼────│  │
│  │ meeting.. │●compl │whisper │ 4m 32s │ 87 │Mar 20 │ 🗑 │  │
│  │ ab3c8f..  │●run   │auto    │   -    │  - │Mar 20 │ ✕  │  │
│  │ interview │●pend  │whisper │   -    │  - │Mar 19 │ ✕  │  │
│  │ podcast.. │●fail  │tiny    │ 1h 2m  │  0 │Mar 19 │ 🗑 │  │
│  │ call-rec. │●cancl │auto    │ 12m 5s │  0 │Mar 18 │ 🗑 │  │
│  │───────────┴───────┴────────┴────────┴────┴───────┴────│  │
│  │ Showing 5 jobs                        [Load More]      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Mobile Layout

```
┌────────────────────────────────┐
│ Batch Jobs      [+ Submit Job] │
│                                │
│ ┌────────────────────────────┐ │
│ │ meeting-transcript         │ │
│ │ ab3c8f · Mar 20  ● Compl. │ │
│ │ Model: whisper  Dur: 4m   │ │
│ │ Segments: 87               │ │
│ │              [🗑 Delete]    │ │
│ └────────────────────────────┘ │
│ ┌────────────────────────────┐ │
│ │ ab3c8f1d...                │ │
│ │ Mar 20, 3:45 PM  ● Run    │ │
│ │ Model: auto  Dur: -       │ │
│ │ Segments: -                │ │
│ │              [✕ Cancel]    │ │
│ └────────────────────────────┘ │
└────────────────────────────────┘
```

## Elements

| Element | Description |
|---------|-------------|
| Page header | Title + subtitle on left, "Submit Job" primary button on right |
| Card header | "All Jobs" title + 3 filter dropdowns |
| Status filter | All / Pending / Running / Completed / Failed / Cancelled |
| Sort filter | Newest first / Oldest first |
| Page size | 20 / 50 / 100 |
| Table columns | Name (with display_name or truncated ID), Status badge, Model name, Duration, Segments, Created date, Actions |
| Actions column | Cancel button (amber, for pending/running) + Delete button (red, for terminal states) |
| Load More footer | Shows count + "Load More" button for infinite scroll |
| Cancel dialog | Confirmation modal with job ID, cancel/confirm buttons |
| Delete dialog | Confirmation modal with job ID, cancel/delete buttons |
| Success toast | Fixed bottom-right green toast for cancel success (auto-dismiss 3s) |
| Empty state | Centered text + "Submit your first job" button |

## Behaviour

1. **Navigation:** Clicking any row navigates to `/jobs/:jobId` (Job Detail).
2. **Filtering:** Status/sort/limit changes trigger re-fetch via `useJobs` infinite query. State persisted in URL params.
3. **Cancel:** Opens amber confirmation dialog → calls `cancelJob(id)` → invalidates queries → shows success toast.
4. **Delete:** Opens red confirmation dialog → calls `deleteJob(id)` → invalidates queries.
5. **Model names:** Resolved via separate `useTranscriptionModels()` query; falls back to model ID if name not found.
6. **Sticky columns:** Name column sticky left, Actions column sticky right on horizontal scroll.
7. **Action click propagation:** `e.stopPropagation()` prevents row navigation when clicking action buttons.

## States

| State | Visual |
|-------|--------|
| Loading | No skeleton shown (30s stale time prevents flash) |
| Error | Red error message |
| Empty (no filter) | Empty state with "Submit your first job" button |
| Empty (with filter) | "No jobs found matching the filter" |
| Data loaded | Table (desktop) or card list (mobile) |
