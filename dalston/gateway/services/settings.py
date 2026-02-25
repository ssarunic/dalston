"""Settings service for admin-configurable overrides.

Provides CRUD operations on the settings table. Database values override
environment variable defaults. Settings are grouped by namespace and each
setting is validated against a predefined registry of allowed definitions.
"""

from __future__ import annotations

import importlib.metadata
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.config import get_settings
from dalston.db.models import SettingModel

logger = structlog.get_logger()

# Cache TTL in seconds — DB overrides are cached briefly to avoid per-request queries
_CACHE_TTL_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Setting definitions registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingDefinition:
    """Schema for a single admin-configurable setting."""

    namespace: str
    key: str
    label: str
    description: str
    value_type: Literal["int", "float", "bool", "string", "select"]
    default_value: Any
    env_var: str
    min_value: int | float | None = None
    max_value: int | float | None = None
    options: list[str] | None = None
    option_labels: list[str] | None = None  # Human-readable labels for select options


@dataclass(frozen=True)
class NamespaceInfo:
    """Metadata about a settings namespace."""

    namespace: str
    label: str
    description: str
    editable: bool = True


NAMESPACES: list[NamespaceInfo] = [
    NamespaceInfo(
        namespace="rate_limits",
        label="Rate Limits",
        description="Control API request and concurrency limits",
    ),
    NamespaceInfo(
        namespace="engines",
        label="Engines",
        description="Engine availability and timeout behavior",
    ),
    NamespaceInfo(
        namespace="audio",
        label="Audio",
        description="Audio download size and timeout constraints",
    ),
    NamespaceInfo(
        namespace="retention",
        label="Retention",
        description="Data retention cleanup intervals and limits",
    ),
    NamespaceInfo(
        namespace="system",
        label="System",
        description="Infrastructure configuration (read-only)",
        editable=False,
    ),
]

NAMESPACE_MAP: dict[str, NamespaceInfo] = {ns.namespace: ns for ns in NAMESPACES}

SETTING_DEFINITIONS: list[SettingDefinition] = [
    # --- rate_limits ---
    SettingDefinition(
        namespace="rate_limits",
        key="requests_per_minute",
        label="Requests per minute",
        description="Maximum API requests per minute per tenant",
        value_type="int",
        default_value=600,
        env_var="DALSTON_RATE_LIMIT_REQUESTS_PER_MINUTE",
        min_value=1,
        max_value=100000,
    ),
    SettingDefinition(
        namespace="rate_limits",
        key="concurrent_jobs",
        label="Max concurrent batch jobs",
        description="Maximum concurrent batch transcription jobs per tenant",
        value_type="int",
        default_value=10,
        env_var="DALSTON_RATE_LIMIT_CONCURRENT_JOBS",
        min_value=1,
        max_value=1000,
    ),
    SettingDefinition(
        namespace="rate_limits",
        key="concurrent_sessions",
        label="Max concurrent realtime sessions",
        description="Maximum concurrent realtime WebSocket sessions per tenant",
        value_type="int",
        default_value=5,
        env_var="DALSTON_RATE_LIMIT_CONCURRENT_SESSIONS",
        min_value=1,
        max_value=1000,
    ),
    # --- engines ---
    SettingDefinition(
        namespace="engines",
        key="unavailable_behavior",
        label="Unavailable engine behavior",
        description="Action when a required engine is not running",
        value_type="select",
        default_value="fail_fast",
        env_var="DALSTON_ENGINE_UNAVAILABLE_BEHAVIOR",
        options=["fail_fast", "wait"],
        option_labels=["Fail fast", "Wait for engine"],
    ),
    SettingDefinition(
        namespace="engines",
        key="wait_timeout_seconds",
        label="Engine wait timeout (seconds)",
        description="How long to wait for an engine before failing",
        value_type="int",
        default_value=300,
        env_var="DALSTON_ENGINE_WAIT_TIMEOUT_SECONDS",
        min_value=10,
        max_value=3600,
    ),
    # --- audio ---
    SettingDefinition(
        namespace="audio",
        key="url_max_size_gb",
        label="Max audio URL download size (GB)",
        description="Maximum audio file size for URL downloads",
        value_type="float",
        default_value=3.0,
        env_var="DALSTON_AUDIO_URL_MAX_SIZE_GB",
        min_value=0.1,
        max_value=50.0,
    ),
    SettingDefinition(
        namespace="audio",
        key="url_timeout_seconds",
        label="Audio URL download timeout (seconds)",
        description="Timeout for downloading audio from URLs",
        value_type="int",
        default_value=300,
        env_var="DALSTON_AUDIO_URL_TIMEOUT_SECONDS",
        min_value=10,
        max_value=3600,
    ),
    # --- retention ---
    SettingDefinition(
        namespace="retention",
        key="cleanup_interval_seconds",
        label="Cleanup interval (seconds)",
        description="Interval between retention cleanup worker sweeps",
        value_type="int",
        default_value=300,
        env_var="DALSTON_RETENTION_CLEANUP_INTERVAL_SECONDS",
        min_value=60,
        max_value=86400,
    ),
    SettingDefinition(
        namespace="retention",
        key="cleanup_batch_size",
        label="Cleanup batch size",
        description="Maximum jobs to purge per cleanup sweep",
        value_type="int",
        default_value=100,
        env_var="DALSTON_RETENTION_CLEANUP_BATCH_SIZE",
        min_value=1,
        max_value=10000,
    ),
    SettingDefinition(
        namespace="retention",
        key="default_days",
        label="Default retention days",
        description="Default retention when not specified by client",
        value_type="int",
        default_value=30,
        env_var="DALSTON_RETENTION_DEFAULT_DAYS",
        min_value=1,
        max_value=3650,
    ),
]

# Lookup maps for fast access
_DEFINITIONS_BY_NS: dict[str, list[SettingDefinition]] = {}
_DEFINITION_MAP: dict[tuple[str, str], SettingDefinition] = {}
for _defn in SETTING_DEFINITIONS:
    _DEFINITIONS_BY_NS.setdefault(_defn.namespace, []).append(_defn)
    _DEFINITION_MAP[(_defn.namespace, _defn.key)] = _defn

# Map env_var names to their Settings field names for reading defaults
_ENV_TO_SETTINGS_FIELD: dict[str, str] = {
    "DALSTON_RATE_LIMIT_REQUESTS_PER_MINUTE": "rate_limit_requests_per_minute",
    "DALSTON_RATE_LIMIT_CONCURRENT_JOBS": "rate_limit_concurrent_jobs",
    "DALSTON_RATE_LIMIT_CONCURRENT_SESSIONS": "rate_limit_concurrent_sessions",
    "DALSTON_ENGINE_UNAVAILABLE_BEHAVIOR": "engine_unavailable_behavior",
    "DALSTON_ENGINE_WAIT_TIMEOUT_SECONDS": "engine_wait_timeout_seconds",
    "DALSTON_AUDIO_URL_MAX_SIZE_GB": "audio_url_max_size_gb",
    "DALSTON_AUDIO_URL_TIMEOUT_SECONDS": "audio_url_timeout_seconds",
    "DALSTON_RETENTION_CLEANUP_INTERVAL_SECONDS": "retention_cleanup_interval_seconds",
    "DALSTON_RETENTION_CLEANUP_BATCH_SIZE": "retention_cleanup_batch_size",
    "DALSTON_RETENTION_DEFAULT_DAYS": "retention_default_days",
}


# ---------------------------------------------------------------------------
# Data types returned to callers
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSetting:
    """A setting with its resolved value and metadata."""

    key: str
    label: str
    description: str
    value_type: str
    value: Any
    default_value: Any
    is_overridden: bool
    env_var: str
    min_value: int | float | None = None
    max_value: int | float | None = None
    options: list[str] | None = None
    option_labels: list[str] | None = None


@dataclass
class NamespaceSettings:
    """All settings in a namespace with resolved values."""

    namespace: str
    label: str
    description: str
    editable: bool
    settings: list[ResolvedSetting]
    updated_at: datetime | None = None


@dataclass
class NamespaceUpdateResult:
    """Result of an update or reset operation, including old values for audit."""

    namespace_settings: NamespaceSettings
    old_values: dict[str, Any]


@dataclass
class NamespaceSummary:
    """Summary of a namespace for the listing endpoint."""

    namespace: str
    label: str
    description: str
    editable: bool
    setting_count: int
    has_overrides: bool


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Cached DB overrides for one namespace."""

    rows: dict[str, Any]
    updated_at: datetime | None
    fetched_at: float


@dataclass
class _SettingsCache:
    """Simple TTL cache for DB settings."""

    entries: dict[str, _CacheEntry] = field(default_factory=dict)
    ttl: float = _CACHE_TTL_SECONDS

    def get(self, cache_key: str) -> _CacheEntry | None:
        entry = self.entries.get(cache_key)
        if entry and (time.monotonic() - entry.fetched_at) < self.ttl:
            return entry
        return None

    def put(
        self,
        cache_key: str,
        rows: dict[str, Any],
        updated_at: datetime | None,
    ) -> None:
        self.entries[cache_key] = _CacheEntry(
            rows=rows,
            updated_at=updated_at,
            fetched_at=time.monotonic(),
        )

    def invalidate(self, cache_key: str) -> None:
        self.entries.pop(cache_key, None)

    def clear(self) -> None:
        self.entries.clear()


_cache = _SettingsCache()


def get_settings_cache() -> _SettingsCache:
    """Get the module-level settings cache (for testing)."""
    return _cache


def clear_settings_cache() -> None:
    """Clear the settings cache."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SettingsService:
    """Service for admin-configurable settings.

    Database rows override environment variable defaults. The resolution
    order is:

        1. DB row for (tenant_id, namespace, key)
        2. DB row for (NULL, namespace, key) — system-wide override
        3. Environment variable (via config.py Settings class)
        4. Hardcoded default in SettingDefinition
    """

    def _get_env_default(self, defn: SettingDefinition) -> Any:
        """Read the current default from env vars (via Pydantic Settings)."""
        settings = get_settings()
        field_name = _ENV_TO_SETTINGS_FIELD.get(defn.env_var)
        if field_name and hasattr(settings, field_name):
            return getattr(settings, field_name)
        return defn.default_value

    async def _fetch_namespace_rows(
        self,
        db: AsyncSession,
        namespace: str,
        tenant_id: UUID | None,
    ) -> tuple[dict[str, Any], datetime | None]:
        """Fetch all DB overrides for a namespace, with caching."""
        cache_key = f"{tenant_id}:{namespace}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached.rows, cached.updated_at

        query = select(SettingModel).where(
            and_(
                SettingModel.namespace == namespace,
                SettingModel.tenant_id == tenant_id
                if tenant_id is not None
                else SettingModel.tenant_id.is_(None),
            )
        )
        result = await db.execute(query)
        rows = list(result.scalars().all())

        overrides: dict[str, Any] = {}
        latest_updated: datetime | None = None
        for row in rows:
            # The value column stores {"v": <actual_value>}
            overrides[row.key] = (
                row.value.get("v") if isinstance(row.value, dict) else row.value
            )
            if latest_updated is None or row.updated_at > latest_updated:
                latest_updated = row.updated_at

        _cache.put(cache_key, overrides, latest_updated)
        return overrides, latest_updated

    def _resolve(
        self,
        defn: SettingDefinition,
        overrides: dict[str, Any],
    ) -> ResolvedSetting:
        """Resolve a single setting to its effective value."""
        env_default = self._get_env_default(defn)
        is_overridden = defn.key in overrides
        value = overrides[defn.key] if is_overridden else env_default

        return ResolvedSetting(
            key=defn.key,
            label=defn.label,
            description=defn.description,
            value_type=defn.value_type,
            value=value,
            default_value=env_default,
            is_overridden=is_overridden,
            env_var=defn.env_var,
            min_value=defn.min_value,
            max_value=defn.max_value,
            options=defn.options,
            option_labels=defn.option_labels,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_namespaces(
        self,
        db: AsyncSession,
        tenant_id: UUID | None = None,
    ) -> list[NamespaceSummary]:
        """List all namespaces with override counts."""
        summaries: list[NamespaceSummary] = []
        for ns_info in NAMESPACES:
            definitions = _DEFINITIONS_BY_NS.get(ns_info.namespace, [])
            overrides, _ = await self._fetch_namespace_rows(
                db, ns_info.namespace, tenant_id
            )
            summaries.append(
                NamespaceSummary(
                    namespace=ns_info.namespace,
                    label=ns_info.label,
                    description=ns_info.description,
                    editable=ns_info.editable,
                    setting_count=len(definitions),
                    has_overrides=len(overrides) > 0,
                )
            )
        return summaries

    async def get_namespace(
        self,
        db: AsyncSession,
        namespace: str,
        tenant_id: UUID | None = None,
    ) -> NamespaceSettings | None:
        """Get all settings in a namespace with resolved values."""
        ns_info = NAMESPACE_MAP.get(namespace)
        if ns_info is None:
            return None

        # System namespace is special — return read-only infra settings
        if namespace == "system":
            return self._get_system_info()

        definitions = _DEFINITIONS_BY_NS.get(namespace, [])
        overrides, updated_at = await self._fetch_namespace_rows(
            db, namespace, tenant_id
        )

        resolved = [self._resolve(defn, overrides) for defn in definitions]

        return NamespaceSettings(
            namespace=namespace,
            label=ns_info.label,
            description=ns_info.description,
            editable=ns_info.editable,
            settings=resolved,
            updated_at=updated_at,
        )

    async def update_namespace(
        self,
        db: AsyncSession,
        namespace: str,
        updates: dict[str, Any],
        updated_by: UUID,
        tenant_id: UUID | None = None,
        expected_updated_at: datetime | None = None,
    ) -> NamespaceUpdateResult:
        """Update settings in a namespace.

        Args:
            db: Database session.
            namespace: Setting namespace.
            updates: Mapping of key -> new value.
            updated_by: API key UUID making the change.
            tenant_id: Tenant scope (None = system-wide).
            expected_updated_at: For optimistic locking — if the namespace
                was modified after this timestamp, raise ValueError.

        Returns:
            Update result with namespace settings and old values for audit.

        Raises:
            ValueError: If namespace is not editable, key is unknown,
                value fails validation, or optimistic lock conflict.
        """
        ns_info = NAMESPACE_MAP.get(namespace)
        if ns_info is None:
            raise ValueError(f"Unknown namespace: {namespace}")
        if not ns_info.editable:
            raise ValueError(f"Namespace '{namespace}' is read-only")

        # Validate all keys and values before making any changes
        for key, value in updates.items():
            defn = _DEFINITION_MAP.get((namespace, key))
            if defn is None:
                raise ValueError(f"Unknown setting: {namespace}/{key}")
            self._validate_value(defn, value)

        # Optimistic locking check
        if expected_updated_at is not None:
            _, current_updated_at = await self._fetch_namespace_rows(
                db, namespace, tenant_id
            )
            if (
                current_updated_at is not None
                and current_updated_at > expected_updated_at
            ):
                raise ConflictError(
                    "Settings were modified by another admin. Please refresh and try again."
                )

        # Upsert each setting
        old_values: dict[str, Any] = {}
        for key, value in updates.items():
            # Fetch existing row
            query = select(SettingModel).where(
                and_(
                    SettingModel.namespace == namespace,
                    SettingModel.key == key,
                    SettingModel.tenant_id == tenant_id
                    if tenant_id is not None
                    else SettingModel.tenant_id.is_(None),
                )
            )
            result = await db.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                old_values[key] = (
                    existing.value.get("v")
                    if isinstance(existing.value, dict)
                    else existing.value
                )
                existing.value = {"v": value}
                existing.updated_by = updated_by
            else:
                defn = _DEFINITION_MAP[(namespace, key)]
                old_values[key] = self._get_env_default(defn)
                db.add(
                    SettingModel(
                        tenant_id=tenant_id,
                        namespace=namespace,
                        key=key,
                        value={"v": value},
                        updated_by=updated_by,
                    )
                )

        await db.commit()

        # Invalidate cache for this namespace
        cache_key = f"{tenant_id}:{namespace}"
        _cache.invalidate(cache_key)

        # Return updated namespace with old values for audit
        ns = await self.get_namespace(db, namespace, tenant_id)
        assert ns is not None
        return NamespaceUpdateResult(namespace_settings=ns, old_values=old_values)

    async def reset_namespace(
        self,
        db: AsyncSession,
        namespace: str,
        tenant_id: UUID | None = None,
    ) -> NamespaceUpdateResult:
        """Delete all DB overrides for a namespace, reverting to defaults.

        Returns:
            Update result with namespace settings and old values for audit.

        Raises:
            ValueError: If namespace is not editable or unknown.
        """
        ns_info = NAMESPACE_MAP.get(namespace)
        if ns_info is None:
            raise ValueError(f"Unknown namespace: {namespace}")
        if not ns_info.editable:
            raise ValueError(f"Namespace '{namespace}' is read-only")

        # Collect old values for audit before deleting
        overrides, _ = await self._fetch_namespace_rows(db, namespace, tenant_id)
        old_values = dict(overrides)

        # Delete all rows
        stmt = delete(SettingModel).where(
            and_(
                SettingModel.namespace == namespace,
                SettingModel.tenant_id == tenant_id
                if tenant_id is not None
                else SettingModel.tenant_id.is_(None),
            )
        )
        await db.execute(stmt)
        await db.commit()

        # Invalidate cache
        cache_key = f"{tenant_id}:{namespace}"
        _cache.invalidate(cache_key)

        ns = await self.get_namespace(db, namespace, tenant_id)
        assert ns is not None
        return NamespaceUpdateResult(namespace_settings=ns, old_values=old_values)

    async def get_effective_value(
        self,
        db: AsyncSession,
        namespace: str,
        key: str,
        tenant_id: UUID | None = None,
    ) -> Any:
        """Get the effective value of a single setting.

        Resolution order:
            1. DB override for tenant
            2. Env var default
            3. Hardcoded default
        """
        defn = _DEFINITION_MAP.get((namespace, key))
        if defn is None:
            raise ValueError(f"Unknown setting: {namespace}/{key}")

        overrides, _ = await self._fetch_namespace_rows(db, namespace, tenant_id)
        if key in overrides:
            return overrides[key]
        return self._get_env_default(defn)

    def _get_system_info(self) -> NamespaceSettings:
        """Return read-only system infrastructure info."""
        settings = get_settings()

        # Mask database password
        db_url = settings.database_url
        if "@" in db_url:
            # postgresql+asyncpg://user:pass@host:port/db -> mask pass
            prefix, suffix = db_url.split("@", 1)
            if ":" in prefix:
                scheme_user = prefix.rsplit(":", 1)[0]
                db_url = f"{scheme_user}:****@{suffix}"

        try:
            version = importlib.metadata.version("dalston")
        except importlib.metadata.PackageNotFoundError:
            version = "dev"

        info_items = [
            ("redis_url", "Redis URL", settings.redis_url),
            ("database_url", "Database", db_url),
            ("s3_bucket", "S3 Bucket", settings.s3_bucket),
            ("s3_region", "S3 Region", settings.s3_region),
            ("version", "Version", version),
        ]

        resolved = [
            ResolvedSetting(
                key=key,
                label=label,
                description="",
                value_type="string",
                value=value,
                default_value=value,
                is_overridden=False,
                env_var="",
            )
            for key, label, value in info_items
        ]

        ns_info = NAMESPACE_MAP["system"]
        return NamespaceSettings(
            namespace="system",
            label=ns_info.label,
            description=ns_info.description,
            editable=False,
            settings=resolved,
            updated_at=None,
        )

    @staticmethod
    def _validate_value(defn: SettingDefinition, value: Any) -> None:
        """Validate a setting value against its definition.

        Raises ValueError with a descriptive message on failure.
        """
        if defn.value_type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"{defn.key}: expected integer, got {type(value).__name__}"
                )
            if defn.min_value is not None and value < defn.min_value:
                raise ValueError(f"{defn.key}: minimum value is {defn.min_value}")
            if defn.max_value is not None and value > defn.max_value:
                raise ValueError(f"{defn.key}: maximum value is {defn.max_value}")
        elif defn.value_type == "float":
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(
                    f"{defn.key}: expected number, got {type(value).__name__}"
                )
            if defn.min_value is not None and value < defn.min_value:
                raise ValueError(f"{defn.key}: minimum value is {defn.min_value}")
            if defn.max_value is not None and value > defn.max_value:
                raise ValueError(f"{defn.key}: maximum value is {defn.max_value}")
        elif defn.value_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(
                    f"{defn.key}: expected boolean, got {type(value).__name__}"
                )
        elif defn.value_type == "string":
            if not isinstance(value, str):
                raise ValueError(
                    f"{defn.key}: expected string, got {type(value).__name__}"
                )
        elif defn.value_type == "select":
            if defn.options and value not in defn.options:
                raise ValueError(f"{defn.key}: must be one of {defn.options}")


class ConflictError(Exception):
    """Raised when an optimistic locking conflict is detected."""

    pass
