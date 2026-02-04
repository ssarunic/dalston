# Console Authentication Patterns

## Auth Provider Protocol

The console authentication system uses a pluggable provider pattern. Core defines the interface, implementations provide the behavior.

```python
from typing import Protocol
from fastapi import APIRouter, Request
from pydantic import BaseModel

class ConsoleUser(BaseModel):
    """User info returned by auth provider."""
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    tenant_id: str | None = None
    role: str = "member"  # "admin" | "member"

class AuthStatus(BaseModel):
    """Auth status for frontend."""
    auth_enabled: bool
    user: ConsoleUser | None = None
    providers: list[str] = []  # ["google", "microsoft", ...]

class ConsoleAuthProvider(Protocol):
    """Protocol for console authentication providers."""

    def get_auth_status(self, request: Request) -> AuthStatus:
        """Check if auth is enabled and get current user if logged in."""
        ...

    async def get_current_user(self, request: Request) -> ConsoleUser | None:
        """Extract and validate user from request."""
        ...

    def get_routes(self) -> APIRouter | None:
        """Return auth routes (login, callback, logout) or None."""
        ...

    def is_admin(self, user: ConsoleUser | None) -> bool:
        """Check if user has admin privileges."""
        ...
```

---

## Noop Provider (Core)

The open-source default that allows unrestricted access:

```python
class NoopAuthProvider:
    """No-op auth for internal/single-tenant deployments."""

    def get_auth_status(self, request: Request) -> AuthStatus:
        return AuthStatus(auth_enabled=False, user=None, providers=[])

    async def get_current_user(self, request: Request) -> ConsoleUser | None:
        return None  # No auth = no user context

    def get_routes(self) -> APIRouter | None:
        return None  # No auth routes needed

    def is_admin(self, user: ConsoleUser | None) -> bool:
        return True  # No auth = full access
```

---

## OAuth Provider (Pro)

Commercial implementation with Google OAuth (extensible to other providers):

```python
class OAuthProvider:
    """OAuth-based console authentication."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwt_secret = settings.jwt_secret_key or secrets.token_urlsafe(32)
        self.jwt_algorithm = settings.jwt_algorithm
        self.jwt_expire_days = settings.jwt_expire_days

    def get_auth_status(self, request: Request) -> AuthStatus:
        user = self._get_user_from_cookie(request)
        return AuthStatus(
            auth_enabled=True,
            user=user,
            providers=list(self._get_enabled_providers()),
        )

    async def get_current_user(self, request: Request) -> ConsoleUser | None:
        return self._get_user_from_cookie(request)

    def get_routes(self) -> APIRouter:
        router = APIRouter()

        @router.get("/status")
        async def auth_status(request: Request):
            return self.get_auth_status(request)

        @router.get("/{provider}/login")
        async def oauth_login(provider: str, request: Request):
            # Generate state token, store in cookie, redirect to OAuth provider
            ...

        @router.get("/{provider}/callback")
        async def oauth_callback(provider: str, request: Request, response: Response):
            # Validate state, exchange code, create/update user, set JWT cookie
            ...

        @router.post("/logout")
        async def logout(response: Response):
            response.delete_cookie("console_session")
            return {"ok": True}

        @router.get("/me")
        async def get_me(user: ConsoleUser = Depends(require_console_user)):
            return user

        return router

    def is_admin(self, user: ConsoleUser | None) -> bool:
        if user is None:
            return False
        return user.role == "admin"
```

---

## Provider Loading

Gateway loads the appropriate provider at startup:

```python
# dalston/gateway/auth/__init__.py

from dalston.gateway.auth.protocol import ConsoleAuthProvider
from dalston.gateway.auth.noop import NoopAuthProvider

def get_console_auth_provider(settings: Settings) -> ConsoleAuthProvider:
    """Load Pro auth if available and configured, otherwise use noop."""

    # Check if auth is explicitly enabled
    if not getattr(settings, 'console_auth_enabled', False):
        return NoopAuthProvider()

    # Try to load Pro module
    try:
        from dalston_pro.auth import OAuthProvider
        return OAuthProvider(settings)
    except ImportError:
        # Pro not installed but auth requested - warn and fall back
        import logging
        logging.warning(
            "CONSOLE_AUTH_ENABLED=true but dalston_pro not installed. "
            "Console will be unauthenticated."
        )
        return NoopAuthProvider()
```

---

## Dependency Injection

FastAPI dependencies for protecting console routes:

```python
# dalston/gateway/dependencies.py

from typing import Annotated
from fastapi import Depends, HTTPException, Request

# Provider instance (set at startup)
_console_auth_provider: ConsoleAuthProvider | None = None

def set_console_auth_provider(provider: ConsoleAuthProvider):
    global _console_auth_provider
    _console_auth_provider = provider

def get_console_auth() -> ConsoleAuthProvider:
    if _console_auth_provider is None:
        raise RuntimeError("Console auth provider not initialized")
    return _console_auth_provider

async def get_console_user(
    request: Request,
    auth: ConsoleAuthProvider = Depends(get_console_auth),
) -> ConsoleUser | None:
    """Get current console user (None if auth disabled or not logged in)."""
    return await auth.get_current_user(request)

async def require_console_user(
    request: Request,
    user: ConsoleUser | None = Depends(get_console_user),
    auth: ConsoleAuthProvider = Depends(get_console_auth),
) -> ConsoleUser | None:
    """Require authentication if auth is enabled."""
    status = auth.get_auth_status(request)
    if status.auth_enabled and user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

async def require_admin(
    user: ConsoleUser | None = Depends(require_console_user),
    auth: ConsoleAuthProvider = Depends(get_console_auth),
) -> ConsoleUser | None:
    """Require admin role if auth is enabled."""
    if not auth.is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# Type aliases for route signatures
ConsoleUserDep = Annotated[ConsoleUser | None, Depends(require_console_user)]
AdminUserDep = Annotated[ConsoleUser | None, Depends(require_admin)]
```

---

## Google OAuth Flow

```python
# dalston_pro/auth/oauth/google.py

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

class GoogleOAuthClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, access_token: str) -> dict:
        """Fetch user profile from Google."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()
```

---

## JWT Session Management

```python
# dalston_pro/auth/jwt.py

import jwt
from datetime import datetime, timedelta

def create_session_token(
    user_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expire_days: int = 30,
) -> str:
    """Create JWT session token."""
    payload = {
        "sub": user_id,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=expire_days),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)

def validate_session_token(
    token: str,
    secret_key: str,
    algorithm: str = "HS256",
) -> str | None:
    """Validate JWT and return user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
```

---

## Cookie Settings

```python
# Session cookie configuration

COOKIE_NAME = "console_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds

def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,      # Prevent JavaScript access
        samesite="lax",     # CSRF protection
        secure=True,        # HTTPS only (set False for local dev)
    )

def clear_session_cookie(response: Response):
    response.delete_cookie(key=COOKIE_NAME)
```

---

## Tenant Scoping

Console queries filter by tenant when user is a member (not admin):

```python
@router.get("/dashboard")
async def get_dashboard(
    user: ConsoleUserDep,
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    # Determine tenant filter
    tenant_filter = None
    if user and user.role != "admin":
        tenant_filter = user.tenant_id

    # Get jobs with optional tenant filter
    jobs = await get_recent_jobs(db, tenant_id=tenant_filter, limit=10)

    # ... rest of dashboard logic
```

---

## User Model (Pro)

```python
# dalston_pro/models/user.py

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from dalston.db.base import Base

class ConsoleUserModel(Base):
    __tablename__ = "console_users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    picture: Mapped[str | None] = mapped_column(String(500))

    # OAuth provider info
    oauth_provider: Mapped[str | None] = mapped_column(String(50))
    oauth_id: Mapped[str | None] = mapped_column(String(255), unique=True)

    # Tenant & role
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    role: Mapped[str] = mapped_column(String(20), default="member")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_login_at: Mapped[datetime | None]

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="users")
```

---

## Frontend Auth Context

```typescript
// web/src/contexts/AuthContext.tsx

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { apiClient } from '../api/client'

interface ConsoleUser {
  id: string
  email: string
  name: string | null
  picture: string | null
  tenant_id: string | null
  role: 'admin' | 'member'
}

interface AuthStatus {
  auth_enabled: boolean
  user: ConsoleUser | null
  providers: string[]
}

interface AuthContextType {
  user: ConsoleUser | null
  isLoading: boolean
  isAuthEnabled: boolean
  isAuthenticated: boolean
  isAdmin: boolean
  providers: string[]
  login: (provider: string) => void
  logout: () => Promise<void>
  refreshAuth: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const refreshAuth = async () => {
    try {
      const data = await apiClient.getAuthStatus()
      setStatus(data)
    } catch (error) {
      setStatus({ auth_enabled: false, user: null, providers: [] })
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    refreshAuth()
  }, [])

  const login = (provider: string) => {
    window.location.href = `/api/console/auth/${provider}/login`
  }

  const logout = async () => {
    await apiClient.logout()
    await refreshAuth()
  }

  const value: AuthContextType = {
    user: status?.user ?? null,
    isLoading,
    isAuthEnabled: status?.auth_enabled ?? false,
    isAuthenticated: status?.user !== null,
    isAdmin: status?.user?.role === 'admin',
    providers: status?.providers ?? [],
    login,
    logout,
    refreshAuth,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
```

---

## Protected Route Component

```typescript
// web/src/components/ProtectedRoute.tsx

import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

interface Props {
  children: React.ReactNode
  requireAdmin?: boolean
}

export function ProtectedRoute({ children, requireAdmin = false }: Props) {
  const { isAuthEnabled, isAuthenticated, isAdmin, isLoading } = useAuth()

  if (isLoading) {
    return <LoadingSpinner />
  }

  // No auth enabled = allow all
  if (!isAuthEnabled) {
    return <>{children}</>
  }

  // Auth enabled but not logged in
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // Admin required but not admin
  if (requireAdmin && !isAdmin) {
    return <ForbiddenPage />
  }

  return <>{children}</>
}
```

---

## Configuration

### Environment Variables

```bash
# Core (optional, defaults to disabled)
CONSOLE_AUTH_ENABLED=false

# Pro (required when auth enabled)
CONSOLE_AUTH_ENABLED=true
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
JWT_SECRET_KEY=xxx                    # Auto-generated if not provided
JWT_ALGORITHM=HS256                   # Default
JWT_EXPIRE_DAYS=30                    # Default

# Access control (optional)
CONSOLE_ALLOWED_DOMAIN=yourcompany.com
CONSOLE_ADMIN_EMAILS=admin@example.com,ops@example.com
```

### Settings Model

```python
# dalston/config.py (additions)

class Settings(BaseSettings):
    # ... existing settings ...

    # Console Authentication (Core)
    console_auth_enabled: bool = False

    # OAuth Settings (Pro)
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""

    # JWT Settings (Pro)
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30

    # Access Control (Pro)
    console_allowed_domain: str = ""
    console_admin_emails: list[str] = []
```
