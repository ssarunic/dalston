# M47: SQL Layer Separation

| | |
|---|---|
| **Goal** | Move all SQL queries from API handlers to services, enforcing proper data access layer separation |
| **Duration** | 1-2 days |
| **Dependencies** | None |
| **Deliverable** | New services for console/audit/pii, refactored API handlers with no SQL |
| **Status** | Complete |

## Problem Statement

The API layer contains direct SQLAlchemy queries that violate the architectural guideline:

> "Handlers are glue code only: parse request, call service, format response. No business logic in handlers"

**Current violations (4 files, 12 locations):**

| File | Violations | Complexity |
|------|------------|------------|
| `dalston/gateway/api/console.py` | 8 | High (aggregates, metrics) |
| `dalston/gateway/api/v1/audit.py` | 2 | Medium (filters, pagination) |
| `dalston/gateway/api/v1/pii.py` | 1 | Simple |
| `dalston/gateway/api/v1/models.py` | 1 | Simple |

**Examples of violations:**

- Direct `select()`, `func.count()`, `case()`, `extract()` in handlers
- Query construction with `.where()`, `.order_by()`, `.limit()` in handlers
- Complex aggregation queries for dashboard metrics in handlers

## Solution

Move all SQL to services following existing patterns (stateless classes, AsyncSession per method).

```
Before:
┌────────────┐     ┌─────────┐
│ API Handler├────▶│ Database│  (SQL in handler)
└────────────┘     └─────────┘

After:
┌────────────┐     ┌─────────┐     ┌─────────┐
│ API Handler├────▶│ Service ├────▶│ Database│
└────────────┘     └─────────┘     └─────────┘
```

---

## Implementation

### Phase 1: Create ConsoleService

**New file:** `dalston/gateway/services/console.py`

Encapsulate all console dashboard and metrics queries:

```python
class ConsoleService:
    async def get_dashboard_stats(self, db: AsyncSession) -> DashboardStats
    async def list_jobs_admin(self, db, limit, cursor, status, sort) -> tuple[list[JobModel], bool]
    async def get_job_admin(self, db, job_id) -> JobModel | None
    async def get_job_with_tasks_admin(self, db, job_id) -> JobModel | None
    async def get_hourly_throughput(self, db, hours=24) -> list[ThroughputBucket]
    async def get_success_rates(self, db) -> list[SuccessRateWindow]
    async def get_total_audio_minutes(self, db) -> float
    async def get_total_jobs_count(self, db) -> int
    async def get_engine_task_stats(self, db, engine_id, hours=24) -> EngineTaskStats
```

**Note:** Admin methods do NOT filter by tenant - authorization is checked in handlers via `Permission.CONSOLE_ACCESS`.

### Phase 2: Create AuditQueryService

**New file:** `dalston/gateway/services/audit_query.py`

Named `AuditQueryService` to distinguish from write-side `AuditService` in `dalston/common/audit.py`.

```python
class AuditQueryService:
    async def list_events(
        self, db, tenant_id, *,
        resource_type=None, resource_id=None, action=None, actor_id=None,
        start_time=None, end_time=None, correlation_id=None,
        limit=25, cursor=None, sort="timestamp_desc"
    ) -> AuditListResult

    async def get_resource_trail(
        self, db, tenant_id, resource_type, resource_id, *,
        limit=25, cursor=None
    ) -> AuditListResult
```

### Phase 3: Create PIIEntityTypeService

**New file:** `dalston/gateway/services/pii_entity_types.py`

```python
class PIIEntityTypeService:
    async def list_entity_types(
        self, db, *,
        category=None, defaults_only=False
    ) -> list[PIIEntityTypeModel]
```

### Phase 4: Extend ModelRegistryService

**Modify:** `dalston/gateway/services/model_registry.py`

Add single method:

```python
async def get_model_by_runtime_model_id(
    self, db: AsyncSession, runtime_model_id: str
) -> ModelRegistryModel | None
```

### Phase 5: Update Dependencies

**Modify:** `dalston/gateway/dependencies.py`

Add singleton providers following existing pattern:

```python
_console_service: ConsoleService | None = None
_audit_query_service: AuditQueryService | None = None
_pii_entity_type_service: PIIEntityTypeService | None = None

def get_console_service() -> ConsoleService: ...
def get_audit_query_service() -> AuditQueryService: ...
def get_pii_entity_type_service() -> PIIEntityTypeService: ...
```

### Phase 6: Refactor API Handlers

Remove all SQLAlchemy imports and replace direct queries with service calls:

| File | Changes |
|------|---------|
| `dalston/gateway/api/console.py` | Remove `select`, `func`, `case`, `extract`, `selectinload` imports; inject ConsoleService; call service methods |
| `dalston/gateway/api/v1/audit.py` | Remove `select` import; inject AuditQueryService; call service methods |
| `dalston/gateway/api/v1/pii.py` | Remove `select` import; inject PIIEntityTypeService; call service method |
| `dalston/gateway/api/v1/models.py` | Remove local `select` import; use existing ModelRegistryService |

---

## Files Changed

| File | Action |
|------|--------|
| `dalston/gateway/services/console.py` | Create |
| `dalston/gateway/services/audit_query.py` | Create |
| `dalston/gateway/services/pii_entity_types.py` | Create |
| `dalston/gateway/services/model_registry.py` | Add 1 method |
| `dalston/gateway/dependencies.py` | Add 3 providers |
| `dalston/gateway/api/console.py` | Remove SQL |
| `dalston/gateway/api/v1/audit.py` | Remove SQL |
| `dalston/gateway/api/v1/pii.py` | Remove SQL |
| `dalston/gateway/api/v1/models.py` | Remove SQL |

---

## Verification

1. `make lint` - No ruff/mypy errors
2. `make test` - All tests pass
3. Manual endpoint tests:
   - `GET /console/dashboard` - Dashboard loads with stats
   - `GET /console/jobs` - Job list with pagination
   - `GET /console/metrics` - Metrics with throughput/success rates
   - `GET /v1/audit` - Audit events with filters
   - `GET /v1/audit/resources/{type}/{id}` - Resource audit trail
   - `GET /v1/pii/entity-types` - PII entity types list
   - `POST /v1/models/hf-resolve` - HF resolution with auto-register

---

## Design Notes

### Why not a repository layer?

The existing services already act as the data access layer. Adding repositories would:

- Add another abstraction layer without clear benefit
- Require refactoring working code
- Diverge from established project patterns

Services encapsulating SQL is the correct boundary for this codebase.

### Authorization pattern

Admin console methods (no tenant filtering) remain in a separate service. Authorization is enforced at the handler level with `Permission.CONSOLE_ACCESS` before calling service methods. This follows the existing `_authorized` suffix pattern used elsewhere.
