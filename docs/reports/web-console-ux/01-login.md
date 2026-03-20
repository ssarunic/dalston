# 01 — Login Page

**Route:** `/login`
**Component:** `src/pages/Login.tsx`
**Auth required:** No (only unauthenticated route)

## Purpose

Single entry point for authentication. Users enter an API key to access the console.

## Layout

Centered card on full-screen dark background. No sidebar or navigation.

## Storyboard

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│                                                         │
│           ┌───────────────────────────────┐             │
│           │                               │             │
│           │         ( 🔑 icon )           │             │
│           │                               │             │
│           │      Dalston Console          │             │
│           │  Enter your API key to sign   │             │
│           │  in to the admin console.     │             │
│           │                               │             │
│           │  API Key                      │             │
│           │  ┌─────────────────────────┐  │             │
│           │  │ ●●●●●●●●●●●●●●●●●●●●  │  │             │
│           │  └─────────────────────────┘  │             │
│           │                               │             │
│           │  ⚠ Invalid API key (error)    │             │
│           │                               │             │
│           │  ┌─────────────────────────┐  │             │
│           │  │        Sign In          │  │             │
│           │  └─────────────────────────┘  │             │
│           │                               │             │
│           │  Need a key? Run:             │             │
│           │  dalston keys create          │             │
│           │                               │             │
│           └───────────────────────────────┘             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Elements

| Element | Type | Description |
|---------|------|-------------|
| Key icon | Circle with KeyRound icon | Visual anchor, `bg-primary/10` background |
| Title | `text-2xl` heading | "Dalston Console" |
| Instructions | `text-sm text-muted-foreground` | Explains what to do |
| API Key input | Password field | `type="password"`, autofocus, placeholder text |
| Error message | Inline text | Red with AlertCircle icon, hidden until validation fails |
| Submit button | Primary button | Full-width, disabled when empty or loading, shows "Validating..." during auth |
| CLI hint | Muted text + code block | Shows `dalston keys create` command |

## Behaviour

1. User enters API key → clicks "Sign In" or presses Enter.
2. `login()` from AuthContext validates the key against the backend (`GET /health` with auth header).
3. **Success:** API key stored in localStorage, redirect to `/` (Dashboard).
4. **Failure:** Inline error message appears below the input, input retains focus.
5. Button is disabled while loading or when input is empty.

## States

| State | Visual |
|-------|--------|
| Empty | Button disabled, no error |
| Loading | Button shows "Validating...", input disabled |
| Error | Red error text with icon below input |
| Success | Redirect (no visual state on this page) |
