# Project structure

This is an orientation map, not a generated file inventory. Use `rg --files`
and the package manifests when exact contents matter.

```text
dalston/
├── dalston/                 Python control plane and shared runtimes
│   ├── common/              Shared models, streams, Redis/S3, registry helpers
│   ├── db/                  SQLAlchemy models and database sessions
│   ├── engine_sdk/          Batch engine runtime, managers, local runner
│   ├── gateway/             FastAPI HTTP/WebSocket API and services
│   ├── orchestrator/        DAG scheduling, events, Lite runtime, RT coordinator
│   ├── realtime_sdk/        Realtime worker/session runtime
│   ├── schemas/             Typed engine input/output schemas
│   └── tools/               Profiling and operational utilities
├── engines/                 Authored pluggable engines and metadata
├── cli/                     `dalston` command-line package and tests
├── sdk/                     `dalston-sdk` Python package and tests
├── web/                     React/TypeScript operator console
├── tests/                   Unit, integration, e2e, web, and benchmark tests
├── docker/                  Control-plane and engine base Dockerfiles
├── infra/                   Cloud templates and deployment scripts
├── alembic/                 Distributed PostgreSQL migrations
├── docs/                    Guides, current references, ADRs, and history
├── scripts/                 Validation, compatibility, and operations scripts
├── docker-compose.yml       Local distributed composition
├── pyproject.toml           Root Python package/tool configuration
└── Makefile                 Supported development workflows
```

## Control plane

### `dalston/gateway`

`main.py` creates the FastAPI application. `api/v1` contains public native and
compatibility routes; `api/console.py` contains console aggregation routes.
Handlers authenticate/validate and delegate to `services/`. Middleware owns
auth, correlation IDs, metrics, and error translation.

The gateway also embeds the realtime `SessionCoordinator` from
`dalston/orchestrator/session_coordinator.py`. There is no standalone
session-router source package or container in the current architecture.

### `dalston/orchestrator`

The distributed orchestrator builds task DAGs, consumes durable events,
schedules stage tasks, handles cancellation/retries, assembles final results,
and runs cleanup/reconciliation loops. `lite_main.py` provides the
single-process Lite execution path.

Final transcript assembly is orchestrator logic, not a queue-backed `merge`
service.

### `dalston/common`, `db`, and `schemas`

These packages define cross-component contracts. Changes to stream names,
statuses, retention semantics, engine schemas, or persistence models usually
require synchronized updates in gateway, orchestrator, SDK/tests, and docs.

## Engine runtimes

`dalston/engine_sdk` provides batch execution, artifact materialization, model
management, inference helpers, cache management, and the local engine runner.
`dalston/realtime_sdk` provides realtime registration, capacity, VAD/session
handling, lag enforcement, and the internal worker WebSocket protocol.

Authored engines are grouped by stage:

```text
engines/
├── stt-prepare/
├── stt-transcribe/
├── stt-align/
├── stt-diarize/
├── stt-detect/
├── stt-redact/
└── stt-combo/
```

Each active engine directory normally owns `engine.yaml`, implementation code,
dependencies, tests where appropriate, and a `Dockerfile`. Some historical
engine directories can remain in the tree without being present in the current
Compose service set; `docker compose config --services` is authoritative for
local deployment.

## Clients and console

- `cli/dalston_cli` implements the Typer CLI, output formatting, configuration,
  and Lite bootstrap.
- `sdk/dalston_sdk` implements typed sync/async HTTP clients, realtime clients,
  and webhook verification.
- `web/src` contains the console pages, shared components, API client/types,
  hooks, and styling. Vite builds `web/dist`, which the gateway serves.

These packages have their own manifests and focused test directories.

## Tests

| Directory | Purpose |
| --- | --- |
| `tests/unit` | Fast isolated backend/runtime behavior |
| `tests/integration` | Component boundaries, protocol contracts, persistence |
| `tests/e2e` | Live-stack/API compatibility flows |
| `tests/web` | Browser/UI behavior |
| `tests/benchmarks` | Load/performance experiments |
| `cli/tests` | CLI package behavior |
| `sdk/tests` | SDK package behavior |

The default `pytest` configuration excludes tests marked `e2e`. Use
`pytest -m e2e` only with the required live stack.

## Development workflows

Prefer Make targets:

```bash
make help
make dev
make dev-minimal
make validate
make health
make stop
```

For local Python development, install
`.[gateway,orchestrator,dev]`. Do not run Docker and local Python copies of the
same gateway/orchestrator simultaneously.

Quality commands:

```bash
pytest
ruff check .
ruff format --check .
pre-commit run --all-files
npm run build --prefix web
npm run lint --prefix web
```

## Documentation classes

- `docs/guides`: task-oriented user/operator documentation.
- `docs/specs`: current public and architecture references.
- `docs/specs/implementations`: internal engineering patterns, not public API
  guarantees.
- `docs/decisions`: architecture decision records.
- `docs/plan`, `docs/reports`, and milestone/test-plan material: historical or
  delivery context, not current user documentation.
