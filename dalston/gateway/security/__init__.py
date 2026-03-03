"""Security module for centralized authorization (M45).

This module provides:
- Principal abstraction for authenticated entities
- Permission enum for fine-grained access control
- SecurityManager for centralized policy enforcement
- Typed security exceptions with HTTP mapping
"""

from dalston.gateway.security.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitExceededError,
    ResourceNotFoundError,
    SecurityError,
)
from dalston.gateway.security.manager import SecurityManager, get_security_manager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal, PrincipalType
from dalston.gateway.security.public_endpoints import (
    OPTIONAL_AUTH_ENDPOINTS,
    PUBLIC_ENDPOINTS,
    is_public_endpoint,
)

__all__ = [
    # Principal
    "Principal",
    "PrincipalType",
    # Permissions
    "Permission",
    # Manager
    "SecurityManager",
    "get_security_manager",
    # Exceptions
    "SecurityError",
    "AuthenticationError",
    "AuthorizationError",
    "ResourceNotFoundError",
    "RateLimitExceededError",
    # Public endpoints
    "PUBLIC_ENDPOINTS",
    "OPTIONAL_AUTH_ENDPOINTS",
    "is_public_endpoint",
]
