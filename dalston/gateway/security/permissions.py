"""Permission definitions for fine-grained access control (M45).

Permissions represent specific actions that can be performed on resources.
They provide finer granularity than scopes, enabling features like
ownership-based access control.

Scope to Permission mapping is defined in the SecurityManager.
"""

from enum import Enum


class Permission(str, Enum):
    """Fine-grained permissions for resource operations.

    Permissions follow the pattern: {resource}:{action}
    Some permissions have :own variants for ownership-restricted access.
    """

    # Job permissions
    JOB_CREATE = "job:create"
    JOB_READ = "job:read"
    JOB_READ_OWN = "job:read:own"
    JOB_UPDATE = "job:update"
    JOB_UPDATE_OWN = "job:update:own"
    JOB_DELETE = "job:delete"
    JOB_DELETE_OWN = "job:delete:own"
    JOB_CANCEL = "job:cancel"
    JOB_CANCEL_OWN = "job:cancel:own"

    # Realtime session permissions
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_READ_OWN = "session:read:own"

    # Webhook permissions
    WEBHOOK_CREATE = "webhook:create"
    WEBHOOK_READ = "webhook:read"
    WEBHOOK_UPDATE = "webhook:update"
    WEBHOOK_DELETE = "webhook:delete"

    # Model permissions
    MODEL_READ = "model:read"
    MODEL_PULL = "model:pull"
    MODEL_DELETE = "model:delete"
    MODEL_SYNC = "model:sync"

    # Admin permissions
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    API_KEY_LIST = "api_key:list"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    AUDIT_READ = "audit:read"
    CONSOLE_ACCESS = "console:access"
