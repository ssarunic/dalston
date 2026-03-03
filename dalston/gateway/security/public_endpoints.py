"""Public endpoints allowlist for deny-by-default enforcement (M45).

This module defines which endpoints are publicly accessible without
authentication. All other endpoints require authentication by default.

Public endpoints should be minimal and include only:
- Health checks (required for infrastructure)
- Documentation (can be disabled in production)
- Metrics (typically protected separately)
"""

# Endpoints that require NO authentication
PUBLIC_ENDPOINTS: set[str] = {
    # Health checks (required for load balancers and k8s probes)
    "/health",
    "/healthz",
    "/ready",
    # Prometheus metrics (often protected by separate network policies)
    "/metrics",
    # OpenAPI documentation (can be disabled via DOCS_ENABLED=false)
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Endpoints with optional authentication
# These work without auth but may provide more data with auth
OPTIONAL_AUTH_ENDPOINTS: set[str] = {
    # Public model catalog (read-only listing)
    "/v1/models",
    "/v1/models/{model_id}",
}


def is_public_endpoint(path: str) -> bool:
    """Check if endpoint is in public allowlist.

    Args:
        path: Request path (e.g., "/health", "/v1/models")

    Returns:
        True if endpoint is public (no auth required)
    """
    # Exact match
    if path in PUBLIC_ENDPOINTS:
        return True

    # Prefix match for docs
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True

    return False


def is_optional_auth_endpoint(path: str) -> bool:
    """Check if endpoint has optional authentication.

    Args:
        path: Request path

    Returns:
        True if endpoint works without auth but benefits from it
    """
    if path in OPTIONAL_AUTH_ENDPOINTS:
        return True

    # Pattern matching for parameterized paths
    # /v1/models/{model_id} matches /v1/models/anything
    if path.startswith("/v1/models/") and "/pull" not in path:
        return True

    return False
