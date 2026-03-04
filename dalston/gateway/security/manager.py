"""Centralized security manager for authorization decisions (M45).

The SecurityManager is the single source of truth for all authorization
decisions in the application. All permission checks should go through
this class to ensure consistent policy enforcement.
"""

from typing import Literal
from uuid import UUID

import structlog

from dalston.gateway.security.exceptions import (
    AuthorizationError,
    ResourceNotFoundError,
)
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import Scope

logger = structlog.get_logger()


# Scope to permission mapping
# Each scope grants a specific set of permissions
SCOPE_PERMISSIONS: dict[Scope, set[Permission]] = {
    Scope.JOBS_READ: {
        Permission.JOB_READ,
        Permission.JOB_READ_OWN,
        Permission.SESSION_READ,
        Permission.SESSION_READ_OWN,
        Permission.MODEL_READ,
    },
    Scope.JOBS_WRITE: {
        Permission.JOB_CREATE,
        Permission.JOB_UPDATE_OWN,
        Permission.JOB_DELETE_OWN,
        Permission.JOB_CANCEL_OWN,
    },
    Scope.REALTIME: {
        Permission.SESSION_CREATE,
    },
    Scope.WEBHOOKS: {
        Permission.WEBHOOK_CREATE,
        Permission.WEBHOOK_READ,
        Permission.WEBHOOK_UPDATE,
        Permission.WEBHOOK_DELETE,
    },
    Scope.ADMIN: set(Permission),  # Admin has all permissions
}


class SecurityManager:
    """Centralized authorization policy engine.

    All permission checks should go through this class to ensure
    consistent policy enforcement across the application.

    The SecurityManager supports three modes:
    - none: All permissions granted (development only)
    - api_key: Check against API key scopes (current production mode)
    - user: Future user-based authentication

    Example:
        >>> manager = get_security_manager()
        >>> manager.require_permission(principal, Permission.JOB_DELETE_OWN)
        >>> # Raises AuthorizationError if permission not granted
    """

    def __init__(
        self,
        mode: Literal["none", "api_key", "user"] = "api_key",
    ):
        """Initialize security manager.

        Args:
            mode: Security mode
                - none: All permissions granted (development only)
                - api_key: Check against API key scopes
                - user: Future user-based auth (not implemented)
        """
        self.mode = mode

    def has_permission(
        self,
        principal: Principal,
        permission: Permission,
    ) -> bool:
        """Check if principal has the required permission.

        Args:
            principal: Authenticated principal
            permission: Required permission

        Returns:
            True if permitted, False otherwise
        """
        if self.mode == "none":
            return True

        # Admin scope grants all permissions
        if Scope.ADMIN in principal.scopes:
            return True

        # Check if any scope grants this permission
        for scope in principal.scopes:
            if permission in SCOPE_PERMISSIONS.get(scope, set()):
                return True

        return False

    def require_permission(
        self,
        principal: Principal,
        permission: Permission,
    ) -> None:
        """Require principal has permission, raise if not.

        Args:
            principal: Authenticated principal
            permission: Required permission

        Raises:
            AuthorizationError: If permission not granted
        """
        if not self.has_permission(principal, permission):
            logger.warning(
                "permission_denied",
                principal_id=str(principal.id),
                principal_type=principal.actor_type,
                permission=permission.value,
            )
            raise AuthorizationError(
                f"Missing required permission: {permission.value}",
                required_permission=permission.value,
            )

    def can_access_resource(
        self,
        principal: Principal,
        resource_tenant_id: UUID,
        resource_created_by: UUID | None = None,
    ) -> bool:
        """Check if principal can access a resource.

        Access is granted if:
        1. Security mode is 'none' (development)
        2. Principal is in the same tenant AND:
           a. Principal has admin scope, OR
           b. Principal is the creator

        Non-admins are denied access when resource has no creator attribution.
        This catches missing attribution bugs early and enforces strict ownership.

        Args:
            principal: Authenticated principal
            resource_tenant_id: Tenant ID of the resource
            resource_created_by: Optional key ID that created the resource

        Returns:
            True if access permitted
        """
        if self.mode == "none":
            return True

        # Must be same tenant
        if principal.tenant_id != resource_tenant_id:
            return False

        # Admin can access all resources in tenant
        if Scope.ADMIN in principal.scopes:
            return True

        # Non-admin: must be the creator (deny if no attribution)
        return principal.id == resource_created_by

    def require_resource_access(
        self,
        principal: Principal,
        resource_tenant_id: UUID,
        resource_type: str,
        resource_id: str | UUID,
        resource_created_by: UUID | None = None,
    ) -> None:
        """Require principal can access resource, raise if not.

        Returns 404 instead of 403 to prevent information leakage
        about resource existence (anti-enumeration).

        Args:
            principal: Authenticated principal
            resource_tenant_id: Tenant ID of the resource
            resource_type: Type of resource (for error message)
            resource_id: ID of resource (for error message)
            resource_created_by: Optional key ID that created the resource

        Raises:
            ResourceNotFoundError: If access not permitted
        """
        if not self.can_access_resource(
            principal, resource_tenant_id, resource_created_by
        ):
            logger.warning(
                "resource_access_denied",
                principal_id=str(principal.id),
                resource_type=resource_type,
                resource_id=str(resource_id),
            )
            raise ResourceNotFoundError(resource_type, resource_id)


# Singleton instance
_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    """Get the global security manager instance.

    The instance is lazily initialized using the security_mode setting
    from the application configuration.

    Returns:
        SecurityManager singleton instance
    """
    global _security_manager
    if _security_manager is None:
        from dalston.config import get_settings

        settings = get_settings()
        # Use security_mode from config if available, default to api_key
        mode: Literal["none", "api_key", "user"] = getattr(
            settings, "security_mode", "api_key"
        )
        _security_manager = SecurityManager(mode=mode)
    return _security_manager


def reset_security_manager() -> None:
    """Reset the security manager singleton (for testing)."""
    global _security_manager
    _security_manager = None
