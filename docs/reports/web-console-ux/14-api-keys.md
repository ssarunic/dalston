# 14 — API Keys

**Route:** `/api-keys`
**Component:** `src/pages/ApiKeys.tsx`
**Auth required:** Yes

## Purpose

Manage API keys used to authenticate with the Dalston API. Create new keys, view existing keys with their scopes and usage, and revoke keys that are no longer needed.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  API Keys                                    [+ Create Key]  │
│  Manage authentication keys                                  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🔑 API Keys          [Active ▾] [Newest ▾] [20 ▾]     │  │
│  │                                                        │  │
│  │  Prefix    │ Name      │ Scopes        │Created│Used│ A│  │
│  │  ──────────┼───────────┼───────────────┼───────┼────┼──│  │
│  │  sk-abc... │ Prod Key  │ admin         │ 5d ago│ 2h │🗑│  │
│  │  [current] │           │ jobs:read     │       │ago │  │  │
│  │            │           │ jobs:write    │       │    │  │  │
│  │  ──────────┼───────────┼───────────────┼───────┼────┼──│  │
│  │  sk-def... │ CI Key    │ jobs:write    │12d ago│ 1d │🗑│  │
│  │            │           │ realtime      │       │ago │  │  │
│  │  ──────────┼───────────┼───────────────┼───────┼────┼──│  │
│  │  sk-xyz... │ Old Key   │ admin         │ 2mo   │never│  │  │
│  │  [revoked] │           │               │ ago   │    │  │  │
│  │                                                        │  │
│  │  Showing 3 of 3 keys                                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🔑 Key Created Successfully                [×]         │  │
│  │                                                        │  │
│  │  Your new API key (shown once):                        │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ sk-abc123def456ghi789jkl012mno345pqr678stu901vw │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  │  ⚠ Copy this key now. It will not be shown again.      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ⚠ Revoke Key                               [×]         │  │
│  │                                                        │  │
│  │  This action cannot be undone. Any systems using this  │  │
│  │  key will lose access immediately.                     │  │
│  │                                                        │  │
│  │  sk-abc...                                             │  │
│  │  Prod Key                                              │  │
│  │                                                        │  │
│  │                         [Cancel]  [Revoke Key]         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Layout

**Desktop:** Full `<Table>` with 6 columns (Prefix, Name, Scopes, Created, Last Used, Actions). Min-width 900px with horizontal scroll. Prefix and Actions columns are sticky.

**Mobile:** Stacked card layout — each key is a bordered card showing prefix, name, badges, created/last-used grid, and revoke button.

## Elements

### Table Filters (in CardHeader)

| Filter | Options | Default |
|--------|---------|---------|
| Status | Active, All, Revoked | Active |
| Sort | Newest first, Oldest first, Last used first, Least recently used | Newest first |
| Rows | 20, 50, 100 | 20 |

All filter state persisted to URL search params via `useSharedTableState`.

### Key Row

| Element | Description |
|---------|-------------|
| Prefix | Monospace `sk-abc...` truncated key prefix |
| Current badge | Secondary badge if key matches the currently authenticated session |
| Revoked badge | Red destructive badge for revoked keys |
| Name | Human-readable key name |
| Scope badges | Color-coded: admin (red), jobs:read (blue), jobs:write (green), realtime (purple), webhooks (orange) |
| Created | Relative time ("5d ago") |
| Last Used | Relative time or "Never" |
| Revoke button | Trash icon, red hover, only shown for non-revoked keys |

Revoked keys render at 50% opacity (desktop) or 60% opacity (mobile).

### Create Key Dialog (`<CreateKeyDialog>`)
Separate component for key creation form.

### Key Created Modal (`<KeyCreatedModal>`)
Shows the full secret key exactly once after creation. Warning that it won't be shown again.

### Revoke Confirmation Dialog

| Element | Description |
|---------|-------------|
| Title | Red "Revoke Key" heading |
| Warning text | Explains this action is irreversible |
| Key preview | Shows prefix + name in muted box |
| Error display | Shown if revoke fails (e.g. "Cannot revoke your own key") |
| Actions | Cancel + "Revoke Key" (destructive, shows "Revoking..." while pending) |

## Behaviour

- Data from `useApiKeys(includeRevoked)` — fetches `/console/auth/keys`.
- Client-side filtering by status (active/revoked/all).
- Client-side sorting by created or last-used date.
- Client-side limit (slice).
- "Showing X of Y keys" footer when keys exist.
- Cannot revoke the currently authenticated key — API returns error, shown inline.
- Loading state: implicit (no explicit skeleton — table just doesn't render).
- Error state: red AlertCircle + "Failed to load API keys".
- Empty state: Key icon + "No keys found" + hint.
