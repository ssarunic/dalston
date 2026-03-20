# Dalston Web Console — UX/UI Review Report

**Date:** 2026-03-20
**Purpose:** Handover document for UX/UI designer review and redesign recommendations.

## Application Summary

Dalston is a self-hosted audio transcription platform. The web console is a React (Vite + TypeScript) management interface for monitoring, configuring, and interacting with the transcription system. It uses a dark theme, Tailwind CSS, shadcn/ui component library, and Lucide icons throughout.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 18 + TypeScript |
| Build | Vite |
| Routing | React Router v6 (BrowserRouter) |
| State | TanStack React Query (server state), React Context (auth, live session) |
| Styling | Tailwind CSS + shadcn/ui component library |
| Icons | Lucide React |
| Data fetching | `ky` HTTP client, WebSocket (real-time) |

## Layout & Navigation

### Desktop (≥768px)
- **Fixed left sidebar** (264px wide) with vertical nav, always visible.
- **Main content area** scrolls independently with `p-6` padding.
- Sidebar header: "DALSTON" title + "Transcription Console" subtitle.
- Sidebar footer: masked API key prefix + Logout button.
- Two floating indicators: **LiveSessionIndicator** (bottom-left, shows active real-time sessions), **DownloadIndicator** (bottom-right, shows download progress).

### Mobile (<768px)
- Sidebar hidden, replaced by a **hamburger menu** in a sticky top header bar.
- Sidebar opens as a **Sheet** (slide-in drawer from left).
- Main content has `p-4` padding.

### Navigation Items (10 items)
1. **Dashboard** (`/`) — LayoutDashboard icon
2. **Batch Jobs** (`/jobs`) — ListTodo icon
3. **Real-time** (`/realtime`) — Radio icon
4. **Engines** (`/engines`) — Server icon
5. **Infrastructure** (`/infrastructure`) — Network icon
6. **Models** (`/models`) — Package icon
7. **API Keys** (`/keys`) — Key icon
8. **Webhooks** (`/webhooks`) — Webhook icon
9. **Audit Log** (`/audit`) — ScrollText icon
10. **Settings** (`/settings`) — Settings icon

Active state: highlighted background (`bg-accent`). Hover: `bg-slate-800/70`.

## Authentication

Single-factor API key authentication. The Login page (`/login`) is the only unauthenticated route. All other routes are wrapped in `<ProtectedRoute>` which checks for a valid API key stored in context/localStorage.

## Page-by-Page Descriptions

Each page is documented in its own storyboard file (01 through 18). See the companion files in this directory:

| # | File | Page |
|---|------|------|
| 01 | `01-login.md` | Login |
| 02 | `02-dashboard.md` | Dashboard |
| 03 | `03-batch-jobs.md` | Batch Jobs List |
| 04 | `04-new-job.md` | Submit New Job |
| 05 | `05-job-detail.md` | Job Detail |
| 06 | `06-task-detail.md` | Task Detail |
| 07 | `07-realtime-sessions.md` | Real-time Sessions List |
| 08 | `08-realtime-session-detail.md` | Real-time Session Detail |
| 09 | `09-realtime-live.md` | Real-time Live (Mic Recording) |
| 10 | `10-engines.md` | Engines Overview |
| 11 | `11-engine-detail.md` | Engine Detail |
| 12 | `12-infrastructure.md` | Infrastructure |
| 13 | `13-models.md` | Model Registry |
| 14 | `14-api-keys.md` | API Keys |
| 15 | `15-webhooks.md` | Webhooks List |
| 16 | `16-webhook-detail.md` | Webhook Detail & Deliveries |
| 17 | `17-audit-log.md` | Audit Log |
| 18 | `18-settings.md` | Settings |

## Cross-Cutting UX Patterns

### Responsive Tables
All list pages (Jobs, Sessions, Keys, Webhooks, Audit Log, Deliveries) use a dual-layout approach:
- **Desktop:** `<Table>` with sticky left/right columns, horizontal scroll (`min-w-[860-980px]`).
- **Mobile:** Stacked card layout with rounded border, condensed grid of metadata fields.

### Filtering & Sorting
Consistent pattern using `useSharedTableState` hook that persists filter/sort/limit state in URL search params:
- **Status filter** — Select dropdown (All / specific statuses)
- **Sort order** — Select dropdown (Newest/Oldest first)
- **Page size** — Select dropdown (20/50/100)

### Pagination
Cursor-based infinite scroll via `useInfiniteQuery`. A `<ListLoadMoreFooter>` component shows "Showing X items" + "Load More" button when `hasNextPage` is true.

### Confirmation Dialogs
Destructive actions (Delete, Cancel, Revoke) always use a confirmation dialog with:
- Description of what will happen
- Target identifier displayed in monospace
- Error display area
- Cancel + Confirm buttons (destructive color)
- Loading state on confirm button

### Empty States
Each list page has a centered empty state with a large muted icon, primary message, and optional secondary message or action button (e.g., "Submit your first job").

### Error States
- Inline error banners with `AlertCircle` icon
- `bg-destructive/10` background with `text-destructive` color
- Form-level and field-level error display

### Status Badges
`<StatusBadge>` component maps statuses (pending, running, completed, failed, cancelled) to colored pill badges used consistently across Jobs and Sessions.

### Back Navigation
`<BackButton>` component with chevron-left icon, uses browser history with fallback path.

### Data Refresh
- `staleTime: 30s` default for all queries (prevents skeleton flash on navigation)
- `refetchOnWindowFocus: true` for live updates
- Some pages have manual refresh buttons (Audit Log, Webhook Deliveries)
- Job/task detail pages auto-poll while in non-terminal state

### String Externalization
All user-facing strings are centralized in `src/lib/strings.ts` (referenced as `S.xxx`), making the app ready for i18n.
