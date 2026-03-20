# 18 — Settings

**Route:** `/settings`
**Component:** `src/pages/Settings.tsx`
**Auth required:** Yes

## Purpose

System configuration page organized by namespace (category). Allows operators to view and edit runtime settings for rate limits, engines, audio processing, retention policies, and view read-only system information.

## Storyboard

### Desktop View

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Settings                                                    │
│  System configuration and runtime settings                   │
│                                                              │
│  ┌─────────┬──────────┬────────┬───────────┬────────┐       │
│  │ ⏱ Rate  │ 🖥 Engine│ 🔊 Aud │ 📦 Retent │ 🖥 Sys │       │
│  │ Limits● │          │  io    │   ion     │  tem   │       │
│  ╞═════════╧══════════╧════════╧═══════════╧════════╡       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                              [↺ Reset to Defaults]     │  │
│  │                                                        │  │
│  │  Max concurrent jobs           ┌────┐                  │  │
│  │                                │ 10 │                  │  │
│  │  Default: 10 · Env: DALSTON... └────┘                  │  │
│  │  ──────────────────────────────────────────────────     │  │
│  │  Rate limit window (seconds)   ┌────┐                  │  │
│  │                            ●   │ 30 │                  │  │
│  │  Default: 60 · Env: DALSTON... └────┘                  │  │
│  │  ──────────────────────────────────────────────────     │  │
│  │  Enable rate limiting          ┌──────────┐            │  │
│  │                                │ ●○ On    │            │  │
│  │  Default: true                 └──────────┘            │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  ● 1 unsaved change              [Cancel]  [Save]     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  ✅ Settings saved successfully                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### System Tab (read-only)

```
│  ┌────────────────────────────────────────────────────────┐  │
│  │  ℹ These settings are read-only and controlled by      │  │
│  │    environment variables or system configuration.       │  │
│  │                                                        │  │
│  │  Instance ID          abc123-def456-ghi789      [📋]   │  │
│  │  ─────────────────────────────────────────────────     │  │
│  │  Version              1.5.2                     [📋]   │  │
│  │  ─────────────────────────────────────────────────     │  │
│  │  Redis URL            redis://localhost:6379     [📋]   │  │
│  │  ─────────────────────────────────────────────────     │  │
│  │  S3 Bucket            dalston-artifacts          [📋]   │  │
│  └────────────────────────────────────────────────────────┘  │
```

### Mobile View

```
│  ┌────────────────────────────────────┐                      │
│  │ [⏱ Rate Limits ●            ▾]    │  (dropdown selector) │
│  └────────────────────────────────────┘                      │
```

## Layout

**Tab Navigation:**
- Desktop (`lg:` and up): Horizontal tab bar with icons. Each tab shows namespace icon + label. Active tab has primary-colored bottom border. Override indicator (small dot) shown for namespaces with non-default values.
- Mobile/Tablet (below `lg:`): Native `<select>` dropdown. Override indicator shown as bullet (•) suffix on label.

**Active tab persisted in URL:** `?tab=rate_limits` via `useSearchParams`.

## Namespace Tabs

| Namespace | Icon | Type |
|-----------|------|------|
| rate_limits | Gauge | Editable |
| engines | Server | Editable |
| audio | AudioLines | Editable |
| retention | Archive | Editable |
| system | Monitor | Read-only |

## Editable Setting Field Types

| Type | Input | Description |
|------|-------|-------------|
| int | `<input type="number" step="1">` | Whole number with optional min/max validation |
| float | `<input type="number" step="any">` | Decimal number with optional min/max validation |
| bool | Toggle switch (custom `<button role="switch">`) | Shows "Enabled"/"Disabled" label |
| select | `<select>` | Dropdown with predefined options and optional labels |

### Setting Field Layout

Each field renders in a 2-column grid (`md:grid-cols-[2fr_3fr]`):
- Left: Description label + blue override dot if value differs from default
- Right: Input control + metadata line ("Default: X · Env: DALSTON_Y") + validation error if any

### Validation

| Rule | Message |
|------|---------|
| Empty int/float | "Value is required" |
| Non-integer for int type | "Must be a whole number" |
| NaN for float type | "Must be a number" |
| Below min_value | "Minimum value is X" |
| Above max_value | "Maximum value is X" |
| Invalid select option | "Must be one of: X, Y, Z" |

Validation runs on field change and again on save.

## Sticky Save Bar

Appears at the bottom when any field has unsaved changes:

| State | Appearance |
|-------|------------|
| Has changes, no errors | Amber dot + "N unsaved change(s)" + Cancel + Save buttons |
| Has changes with validation errors | Red dot + "Fix errors before saving" + Cancel + Save (disabled) |

## Feedback Messages

| Condition | Display |
|-----------|---------|
| Save success | Green banner: "Settings saved successfully" |
| Save error (409 Conflict) | Red banner: concurrency conflict message |
| Save error (400 Bad Request) | Red banner: validation detail from server |
| Save error (other) | Red banner: generic failure message |
| Reset error | Red banner: "Failed to reset settings" |

## Reset to Defaults Dialog

Only shown when the namespace has overridden values.

| Element | Description |
|---------|-------------|
| Title | "Reset {namespace label}" |
| Description | Warning about reverting to defaults |
| Preview | Shows each overridden setting: `key: current → default` in monospace |
| Actions | Cancel + "Reset to Defaults" (destructive, shows "Resetting..." while pending) |

## System Info Tab

Read-only view:
- Blue info banner: "These settings are read-only..."
- Each setting as a label + monospace value + copy-to-clipboard button
- Copy button shows checkmark for 2 seconds after copying

## Behaviour

- Namespace list from `useSettingsNamespaces()`.
- Active namespace data from `useSettingsNamespace(namespace)`.
- Save via `useUpdateSettings(namespace)` — sends only dirty keys with optimistic concurrency control (`expected_updated_at`).
- Reset via `useResetSettings(namespace)`.
- Form state resets when switching tabs (effect watches `data` changes).
- Dirty tracking: compares local `formValues` against server `setting.value`.
- Loading state: returns null (blank) while loading.
- No error state for namespace list — relies on query defaults.
