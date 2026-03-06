"""Principal abstraction for authenticated entities (M45).

A Principal represents an authenticated entity making a request.
It provides a unified interface for both API keys and session tokens.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class PrincipalType(StrEnum):
    """Type of authenticated principal."""

    API_KEY = "api_key"
    SESSION_TOKEN = "session_token"
    SYSTEM = "system"


@dataclass
class Principal:
    """Represents an authenticated entity making a request.

    This abstraction unifies API keys and session tokens, providing
    consistent access to identity and permissions.

    Attributes:
        type: Type of principal (API key, session token, or system)
        id: Unique identifier (API key ID or parent key ID for tokens)
        tenant_id: Tenant the principal belongs to
        scopes: List of granted scopes
        key_prefix: Display prefix for API keys (e.g., "dk_abc123")
        parent_key_id: For session tokens, the ID of the parent API key
    """

    type: PrincipalType
    id: UUID
    tenant_id: UUID
    scopes: list  # list[Scope] - avoid circular import
    key_prefix: str | None = None
    parent_key_id: UUID | None = None

    @classmethod
    def from_api_key(cls, api_key) -> "Principal":
        """Create principal from API key.

        Args:
            api_key: APIKey dataclass from auth service

        Returns:
            Principal instance
        """
        return cls(
            type=PrincipalType.API_KEY,
            id=api_key.id,
            tenant_id=api_key.tenant_id,
            scopes=api_key.scopes,
            key_prefix=api_key.prefix,
        )

    @classmethod
    def from_session_token(cls, token) -> "Principal":
        """Create principal from session token.

        Args:
            token: SessionToken dataclass from auth service

        Returns:
            Principal instance
        """
        return cls(
            type=PrincipalType.SESSION_TOKEN,
            id=token.parent_key_id,  # Use parent key ID as principal ID
            tenant_id=token.tenant_id,
            scopes=token.scopes,
            parent_key_id=token.parent_key_id,
        )

    @classmethod
    def system(cls, tenant_id: UUID) -> "Principal":
        """Create system principal for background operations.

        System principals have admin access and are used for internal
        operations like cleanup workers and scheduled tasks.

        Args:
            tenant_id: Tenant context for the operation

        Returns:
            Principal instance with admin scope
        """
        from dalston.gateway.services.auth import Scope

        return cls(
            type=PrincipalType.SYSTEM,
            id=UUID("00000000-0000-0000-0000-000000000000"),
            tenant_id=tenant_id,
            scopes=[Scope.ADMIN],
        )

    def has_scope(self, scope) -> bool:
        """Check if principal has the required scope.

        Admin scope grants all permissions.

        Args:
            scope: Scope to check

        Returns:
            True if principal has the scope (or admin scope)
        """
        from dalston.gateway.services.auth import Scope

        return Scope.ADMIN in self.scopes or scope in self.scopes

    @property
    def is_admin(self) -> bool:
        """Check if principal has admin scope."""
        from dalston.gateway.services.auth import Scope

        return Scope.ADMIN in self.scopes

    @property
    def actor_id(self) -> str:
        """Return actor ID for audit logging.

        Returns the key prefix if available, otherwise the UUID string.
        """
        if self.key_prefix:
            return self.key_prefix
        return str(self.id)

    @property
    def actor_type(self) -> str:
        """Return actor type for audit logging."""
        return self.type.value
