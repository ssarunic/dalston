"""API Key authentication service.

Handles API key generation, validation, and rate limiting.
API keys are stored in PostgreSQL with SHA256 hashes (never plaintext).
Session tokens and rate limits remain in Redis (ephemeral data).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import APIKeyModel

# Key format: dk_{43 chars} = dk_ prefix + 32 urlsafe bytes (base64 = 43 chars)
KEY_PREFIX = "dk_"
KEY_BYTES = 32

# Session token format: tk_{43 chars} for ephemeral client-side auth
TOKEN_PREFIX = "tk_"
TOKEN_BYTES = 32
DEFAULT_TOKEN_TTL = 600  # 10 minutes

# Redis key patterns (only for session tokens and rate limits now)
REDIS_RATE_LIMIT = "dalston:ratelimit:{key_id}"
REDIS_SESSION_TOKEN = "dalston:session_token:{hash}"

# Rate limit window in seconds
RATE_LIMIT_WINDOW = 60

# Default expiration date (distant future - we avoid nulls)
DEFAULT_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


class Scope(str, Enum):
    """API key permission scopes."""

    JOBS_READ = "jobs:read"
    JOBS_WRITE = "jobs:write"
    REALTIME = "realtime"
    WEBHOOKS = "webhooks"
    ADMIN = "admin"


# Default scopes for new keys
DEFAULT_SCOPES = [Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.REALTIME]


@dataclass
class APIKey:
    """API key data structure."""

    id: UUID
    key_hash: str
    prefix: str  # First 10 chars for display (e.g., "dk_abc1234")
    name: str
    tenant_id: UUID
    scopes: list[Scope]
    rate_limit: int | None  # Requests per minute, None = unlimited
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime  # Expiration date (default: 2099-12-31)
    revoked_at: datetime | None

    @property
    def is_revoked(self) -> bool:
        """Check if key has been revoked."""
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        """Check if key has expired."""
        return datetime.now(UTC) > self.expires_at

    def has_scope(self, scope: Scope) -> bool:
        """Check if key has the specified scope or admin scope."""
        return Scope.ADMIN in self.scopes or scope in self.scopes

    @classmethod
    def from_model(cls, model: APIKeyModel) -> APIKey:
        """Create APIKey from SQLAlchemy model."""
        return cls(
            id=model.id,
            key_hash=model.key_hash,
            prefix=model.prefix,
            name=model.name,
            tenant_id=model.tenant_id,
            scopes=[Scope(s) for s in model.scopes.split(",") if s],
            rate_limit=model.rate_limit,
            created_at=model.created_at,
            last_used_at=model.last_used_at,
            expires_at=model.expires_at if model.expires_at else DEFAULT_EXPIRES_AT,
            revoked_at=model.revoked_at,
        )


@dataclass
class SessionToken:
    """Ephemeral session token for client-side WebSocket auth.

    Session tokens are short-lived and scoped to realtime access only.
    They allow browser clients to connect directly without exposing
    long-lived API keys.
    """

    token_hash: str
    tenant_id: UUID
    parent_key_id: UUID  # The API key that created this token
    scopes: list[Scope]
    expires_at: datetime
    created_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(UTC) > self.expires_at

    def has_scope(self, scope: Scope) -> bool:
        """Check if token has the specified scope."""
        # Session tokens don't get admin escalation
        return scope in self.scopes

    def to_dict(self) -> dict:
        """Convert to dictionary for Redis storage."""
        return {
            "token_hash": self.token_hash,
            "tenant_id": str(self.tenant_id),
            "parent_key_id": str(self.parent_key_id),
            "scopes": ",".join(s.value for s in self.scopes),
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionToken:
        """Create from dictionary (Redis storage)."""
        return cls(
            token_hash=data["token_hash"],
            tenant_id=UUID(data["tenant_id"]),
            parent_key_id=UUID(data["parent_key_id"]),
            scopes=[Scope(s) for s in data["scopes"].split(",") if s],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


def generate_session_token() -> str:
    """Generate a new session token with 256 bits of entropy.

    Returns:
        Session token string in format: tk_{43 urlsafe base64 chars}
    """
    random_bytes = secrets.token_urlsafe(TOKEN_BYTES)
    return f"{TOKEN_PREFIX}{random_bytes}"


def generate_api_key() -> str:
    """Generate a new API key with 256 bits of entropy.

    Returns:
        API key string in format: dk_{43 urlsafe base64 chars}
    """
    random_bytes = secrets.token_urlsafe(KEY_BYTES)
    return f"{KEY_PREFIX}{random_bytes}"


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA256.

    Args:
        key: Raw API key string

    Returns:
        Hex-encoded SHA256 hash
    """
    return hashlib.sha256(key.encode()).hexdigest()


def get_key_prefix(key: str) -> str:
    """Get display prefix from API key (first 10 chars).

    Args:
        key: Raw API key string

    Returns:
        First 10 characters for display
    """
    return key[:10]


class AuthService:
    """Service for API key authentication and management.

    API keys are stored in PostgreSQL for durability.
    Session tokens and rate limits are stored in Redis (ephemeral).
    """

    def __init__(self, db: AsyncSession, redis: Redis):
        """Initialize auth service.

        Args:
            db: Async SQLAlchemy session for API key storage
            redis: Async Redis client for session tokens and rate limits
        """
        self.db = db
        self.redis = redis

    async def create_api_key(
        self,
        name: str,
        tenant_id: UUID,
        scopes: list[Scope] | None = None,
        rate_limit: int | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[str, APIKey]:
        """Create a new API key.

        Args:
            name: Human-readable name for the key
            tenant_id: Tenant UUID for isolation
            scopes: Permission scopes (defaults to DEFAULT_SCOPES)
            rate_limit: Requests per minute limit (None = unlimited)
            expires_at: Expiration date (defaults to DEFAULT_EXPIRES_AT)

        Returns:
            Tuple of (raw_key, APIKey object)
            Note: raw_key is only returned once and cannot be retrieved later
        """
        if scopes is None:
            scopes = list(DEFAULT_SCOPES)
        if expires_at is None:
            expires_at = DEFAULT_EXPIRES_AT

        # Generate key
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        prefix = get_key_prefix(raw_key)

        # Create database model
        model = APIKeyModel(
            id=uuid4(),
            key_hash=key_hash,
            prefix=prefix,
            name=name,
            tenant_id=tenant_id,
            scopes=",".join(s.value for s in scopes),
            rate_limit=rate_limit,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=expires_at,
            revoked_at=None,
        )

        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)

        return raw_key, APIKey.from_model(model)

    async def validate_api_key(self, raw_key: str) -> APIKey | None:
        """Validate an API key and return its metadata.

        Args:
            raw_key: Raw API key string

        Returns:
            APIKey object if valid, None if invalid, revoked, or expired
        """
        if not raw_key or not raw_key.startswith(KEY_PREFIX):
            return None

        key_hash = hash_api_key(raw_key)

        # Fetch from PostgreSQL
        stmt = select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        result = await self.db.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            return None

        api_key = APIKey.from_model(model)

        # Check if revoked or expired
        if api_key.is_revoked or api_key.is_expired:
            return None

        # Update last_used_at
        model.last_used_at = datetime.now(UTC)
        await self.db.commit()

        return api_key

    async def get_api_key_by_id(
        self, key_id: UUID, tenant_id: UUID | None = None
    ) -> APIKey | None:
        """Get API key by its ID.

        Args:
            key_id: API key UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            APIKey object or None if not found
        """
        stmt = select(APIKeyModel).where(APIKeyModel.id == key_id)
        if tenant_id is not None:
            stmt = stmt.where(APIKeyModel.tenant_id == tenant_id)

        result = await self.db.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            return None

        return APIKey.from_model(model)

    async def list_api_keys(
        self,
        tenant_id: UUID,
        include_revoked: bool = False,
    ) -> list[APIKey]:
        """List all API keys for a tenant.

        Args:
            tenant_id: Tenant UUID
            include_revoked: If True, include revoked keys in the list

        Returns:
            List of APIKey objects
        """
        stmt = select(APIKeyModel).where(APIKeyModel.tenant_id == tenant_id)

        if not include_revoked:
            stmt = stmt.where(APIKeyModel.revoked_at.is_(None))

        stmt = stmt.order_by(APIKeyModel.created_at.desc())

        result = await self.db.execute(stmt)
        models = result.scalars().all()

        return [APIKey.from_model(model) for model in models]

    async def revoke_api_key(self, key_id: UUID, tenant_id: UUID | None = None) -> bool:
        """Revoke an API key.

        Args:
            key_id: API key UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            True if revoked, False if not found
        """
        stmt = select(APIKeyModel).where(APIKeyModel.id == key_id)
        if tenant_id is not None:
            stmt = stmt.where(APIKeyModel.tenant_id == tenant_id)

        result = await self.db.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            return False

        model.revoked_at = datetime.now(UTC)
        await self.db.commit()

        return True

    async def check_rate_limit(self, api_key: APIKey) -> tuple[bool, int]:
        """Check and increment rate limit for an API key.

        Args:
            api_key: APIKey object

        Returns:
            Tuple of (allowed, remaining_requests)
            - allowed: True if request is allowed
            - remaining_requests: Number of requests remaining in window
        """
        if api_key.rate_limit is None:
            return True, -1  # Unlimited

        rate_key = REDIS_RATE_LIMIT.format(key_id=api_key.id)

        # Increment counter
        current = await self.redis.incr(rate_key)

        # Set expiry on first request in window
        if current == 1:
            await self.redis.expire(rate_key, RATE_LIMIT_WINDOW)

        remaining = max(0, api_key.rate_limit - current)
        allowed = current <= api_key.rate_limit

        return allowed, remaining

    async def has_any_api_keys(self) -> bool:
        """Check if any API keys exist in the system.

        Returns:
            True if at least one API key exists
        """
        stmt = select(APIKeyModel.id).where(APIKeyModel.revoked_at.is_(None)).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    # Session Token Methods (still using Redis - ephemeral data)

    async def create_session_token(
        self,
        api_key: APIKey,
        ttl: int = DEFAULT_TOKEN_TTL,
        scopes: list[Scope] | None = None,
    ) -> tuple[str, SessionToken]:
        """Create an ephemeral session token for client-side auth.

        Session tokens inherit from the parent API key but:
        - Have a short TTL (default 10 minutes)
        - Cannot exceed parent key's scopes
        - Are typically limited to realtime scope

        Args:
            api_key: Parent API key (must have realtime scope)
            ttl: Time-to-live in seconds (default 600 = 10 minutes)
            scopes: Requested scopes (defaults to [REALTIME], cannot exceed parent)

        Returns:
            Tuple of (raw_token, SessionToken object)
            Note: raw_token is only returned once

        Raises:
            ValueError: If requested scopes exceed parent key's scopes
        """
        # Default to realtime-only scope
        if scopes is None:
            scopes = [Scope.REALTIME]

        # Validate scopes don't exceed parent
        for scope in scopes:
            if not api_key.has_scope(scope):
                raise ValueError(
                    f"Cannot grant scope '{scope.value}' - parent key lacks it"
                )

        # Generate token
        raw_token = generate_session_token()
        token_hash = hash_api_key(raw_token)

        now = datetime.now(UTC)
        session_token = SessionToken(
            token_hash=token_hash,
            tenant_id=api_key.tenant_id,
            parent_key_id=api_key.id,
            scopes=scopes,
            expires_at=datetime.fromtimestamp(now.timestamp() + ttl, tz=UTC),
            created_at=now,
        )

        # Store in Redis with TTL
        token_key = REDIS_SESSION_TOKEN.format(hash=token_hash)
        data = session_token.to_dict()
        await self.redis.hset(token_key, mapping=data)
        await self.redis.expire(token_key, ttl)

        return raw_token, session_token

    async def validate_session_token(self, raw_token: str) -> SessionToken | None:
        """Validate a session token and return its metadata.

        Args:
            raw_token: Raw session token string

        Returns:
            SessionToken object if valid, None if invalid or expired
        """
        if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
            return None

        token_hash = hash_api_key(raw_token)
        token_key = REDIS_SESSION_TOKEN.format(hash=token_hash)

        # Fetch token data
        data = await self.redis.hgetall(token_key)
        if not data:
            return None

        session_token = SessionToken.from_dict(data)

        # Double-check expiry (Redis TTL handles cleanup, but be safe)
        if session_token.is_expired:
            return None

        return session_token

    async def revoke_session_token(self, raw_token: str) -> bool:
        """Revoke a session token immediately.

        Args:
            raw_token: Raw session token string

        Returns:
            True if revoked, False if not found
        """
        if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
            return False

        token_hash = hash_api_key(raw_token)
        token_key = REDIS_SESSION_TOKEN.format(hash=token_hash)

        result = await self.redis.delete(token_key)
        return result > 0
