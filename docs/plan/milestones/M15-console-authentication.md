# M15: Console Authentication

|  |  |
|---|---|
| **Goal** | Add optional OAuth authentication to the web console with role-based access control |
| **Duration** | 3-4 days |
| **Dependencies** | M10 (web console), M11 (API authentication) |
| **Deliverable** | Pluggable auth system with Google OAuth, user accounts, and tenant-scoped access |
| **Status** | Completed |

## User Story

> *"As a Dalston operator, I can secure my web console with Google login and control who can see which jobs based on their tenant membership."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                    CONSOLE AUTHENTICATION                                │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                  ConsoleAuthProvider (Protocol)                     │ │
│  │                                                                     │ │
│  │  + get_current_user(request) -> ConsoleUser | None                 │ │
│  │  + get_auth_status() -> AuthStatus                                 │ │
│  │  + get_routes() -> APIRouter | None                                │ │
│  │  + is_admin(user) -> bool                                          │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ▲                                           │
│               ┌──────────────┴──────────────┐                           │
│               │                             │                           │
│   ┌───────────────────────┐    ┌───────────────────────────────────┐   │
│   │  NoopAuthProvider     │    │  OAuthProvider (Pro)              │   │
│   │  (Core - MIT)         │    │  (Commercial)                     │   │
│   │                       │    │                                   │   │
│   │  - No login required  │    │  - Google, MS, Apple, email      │   │
│   │  - Full access        │    │  - User accounts + JWT sessions  │   │
│   │  - Single tenant      │    │  - Multi-tenant + RBAC           │   │
│   └───────────────────────┘    └───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘

Data Flow (with Auth Enabled):
┌──────────┐    ┌─────────────┐    ┌──────────────┐    ┌───────────────┐
│  Browser │───▶│  /login     │───▶│  Google OAuth│───▶│  JWT Cookie   │
│          │    │  page       │    │  flow        │    │  + User       │
└──────────┘    └─────────────┘    └──────────────┘    └───────────────┘
                                                              │
                                                              ▼
                                                       ┌───────────────┐
                                                       │  Console API  │
                                                       │  filtered by  │
                                                       │  tenant_id    │
                                                       └───────────────┘
```

---

## Licensing Model

This milestone introduces the "open core" split:

| Component        | License    | Features                                               |
|------------------|------------|--------------------------------------------------------|
| **Dalston Core** | Apache 2.0 | Full transcription, API keys, unauthenticated console  |
| **Dalston Pro**  | Commercial | OAuth, user accounts, multi-tenant, RBAC               |

See [LICENSING.md](../../specs/LICENSING.md) for details.

---

## Data Model

### ConsoleUser

| Field | Type | Description |
|-------|------|-------------|
| `id` | uuid | Unique identifier |
| `email` | string | Unique, from OAuth provider |
| `name` | string \| null | Display name |
| `picture` | string \| null | Profile picture URL |
| `oauth_provider` | string | "google", "microsoft", etc. |
| `oauth_id` | string | Provider's unique ID |
| `tenant_id` | uuid | Tenant membership |
| `role` | string | "admin" \| "member" |
| `created_at` | datetime | First login |
| `last_login_at` | datetime | Most recent login |

### AuthStatus

| Field | Type | Description |
|-------|------|-------------|
| `auth_enabled` | bool | Is authentication required? |
| `user` | ConsoleUser \| null | Current user if logged in |
| `providers` | list[string] | Available OAuth providers |

---

## Steps

### 15.1: Auth Protocol (Core)

**Deliverables:**

- Define `ConsoleAuthProvider` protocol in `dalston/gateway/auth/protocol.py`
- Define `ConsoleUser` and `AuthStatus` Pydantic models
- Create `NoopAuthProvider` that always allows access
- Provider loader with Pro module detection

**Files:**

- `dalston/gateway/auth/__init__.py`
- `dalston/gateway/auth/protocol.py`
- `dalston/gateway/auth/noop.py`

---

### 15.2: Console Auth Dependencies (Core)

**Deliverables:**

- `get_console_auth()` - Get auth provider instance
- `get_console_user()` - Extract user from request (None if no auth)
- `require_console_user()` - Require auth if enabled
- `require_admin()` - Require admin role if auth enabled

**Files:**

- `dalston/gateway/dependencies.py` (modify)

---

### 15.3: Update Console Routes (Core)

**Deliverables:**

- Add `ConsoleUserDep` to all `/api/console/*` endpoints
- Add tenant filtering logic (no-op when user is None)
- Add `/api/console/auth/status` endpoint

**Files:**

- `dalston/gateway/api/console.py` (modify)

---

### 15.4: Gateway Auth Integration (Core)

**Deliverables:**

- Initialize auth provider at startup
- Register auth routes if provider has them
- Store provider in app state

**Files:**

- `dalston/gateway/main.py` (modify)

---

### 15.5: Frontend Auth Context (Core)

**Deliverables:**

- `AuthContext` with user state, login/logout functions
- `useAuthStatus` hook fetching `/api/console/auth/status`
- `ProtectedRoute` component (pass-through when auth disabled)
- Login page (shows "no auth required" or provider buttons)

**Files:**

- `web/src/contexts/AuthContext.tsx`
- `web/src/hooks/useAuthStatus.ts`
- `web/src/components/ProtectedRoute.tsx`
- `web/src/pages/Login.tsx`

---

### 15.6: Frontend Integration (Core)

**Deliverables:**

- Wrap App with `AuthProvider`
- Add `/login` route
- Wrap protected routes with `ProtectedRoute`
- Add user profile to sidebar (when authenticated)

**Files:**

- `web/src/App.tsx` (modify)
- `web/src/components/Sidebar.tsx` (modify)
- `web/src/api/client.ts` (modify)
- `web/src/api/types.ts` (modify)

---

### 15.7: OAuth Provider (Pro)

**Deliverables:**

- `OAuthProvider` implementing `ConsoleAuthProvider`
- Google OAuth client (token exchange, user info)
- JWT session management (issue, validate, refresh)
- Auth routes: login, callback, logout, me, status

**Files:**

- `dalston_pro/auth/__init__.py`
- `dalston_pro/auth/provider.py`
- `dalston_pro/auth/oauth/google.py`
- `dalston_pro/auth/jwt.py`
- `dalston_pro/auth/routes.py`

---

### 15.8: User Model & Migration (Pro)

**Deliverables:**

- `ConsoleUserModel` SQLAlchemy model
- Alembic migration for `console_users` table
- User creation/update on OAuth callback

**Files:**

- `dalston_pro/models/user.py`
- `migrations/versions/xxx_add_console_users.py`

---

### 15.9: Tenant Scoping (Pro)

**Deliverables:**

- Filter console queries by `user.tenant_id`
- Admin users see all tenants
- Member users see only their tenant's jobs

**Files:**

- `dalston/gateway/api/console.py` (modify)

---

### 15.10: Self-Service API Keys (Pro)

**Deliverables:**

- `GET /api/console/keys` - List user's tenant API keys
- `POST /api/console/keys` - Create key for user's tenant
- `DELETE /api/console/keys/{id}` - Revoke key
- Frontend UI for key management

**Files:**

- `dalston_pro/api/api_keys.py`
- `web/src/pages/ApiKeys.tsx`

---

## Configuration

### Core (No Auth)

```bash
# No configuration needed - auth disabled by default
```

### Pro (With Auth)

```bash
# Required
CONSOLE_AUTH_ENABLED=true
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
JWT_SECRET_KEY=xxx  # Or auto-generated

# Optional
JWT_EXPIRE_DAYS=30
CONSOLE_ALLOWED_DOMAIN=yourcompany.com
CONSOLE_ADMIN_EMAILS=admin@yourcompany.com
```

---

## Verification

### Core: Auth Disabled

```bash
# Start without Pro module
uvicorn dalston.gateway.main:app

# Console loads without login
curl http://localhost:8000/api/console/auth/status
# → {"auth_enabled": false, "user": null, "providers": []}

# All console endpoints work
curl http://localhost:8000/api/console/dashboard
# → Dashboard data (no auth required)
```

### Pro: Auth Enabled

```bash
# Start with Pro module and Google OAuth configured
CONSOLE_AUTH_ENABLED=true \
GOOGLE_CLIENT_ID=xxx \
GOOGLE_CLIENT_SECRET=xxx \
uvicorn dalston.gateway.main:app

# Check status
curl http://localhost:8000/api/console/auth/status
# → {"auth_enabled": true, "user": null, "providers": ["google"]}

# Console requires login
curl http://localhost:8000/api/console/dashboard
# → 401 Unauthorized

# OAuth flow
# 1. Browser: GET /api/console/auth/google/login → Redirect to Google
# 2. Google: User authenticates → Redirect to callback
# 3. Gateway: GET /api/console/auth/google/callback → Set cookie, redirect to /
# 4. Browser: Console loads with user profile
```

### Role-Based Access

```bash
# Admin user sees all jobs
# Member user sees only their tenant's jobs
# Member can create API keys for their tenant
# Member cannot access admin-only endpoints
```

---

## Checkpoint

**Phase 1 (Core):**

- [ ] Auth protocol and models defined
- [ ] Noop provider implemented
- [ ] Console routes accept optional auth
- [ ] Frontend handles auth disabled gracefully
- [ ] No breaking changes to existing behavior

**Phase 2 (Pro):**

- [ ] Google OAuth working
- [ ] JWT sessions with cookies
- [ ] User model and database
- [ ] Tenant-scoped access
- [ ] Self-service API key management

**Phase 3 (Extended Providers):**

- [ ] Microsoft OAuth
- [ ] Apple OAuth
- [ ] Email magic link
- [ ] Phone OTP

---

## Future Enhancements

- **Tenant Management UI**: Create/edit tenants, invite users
- **Audit Logging**: Track user actions
- **Usage Analytics**: Per-tenant metrics and billing hooks
- **SSO/SAML**: Enterprise identity providers
