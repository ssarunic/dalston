# Repository Guidelines

## Project Structure & Module Organization

- `dalston/`: core backend services and shared modules (`gateway/`, `orchestrator/`, `common/`, `db/`, `session_router/`).
- `tests/`: primary test suite split into `unit/`, `integration/`, and `e2e/`.
- `engines/`: pluggable transcription/diarization/alignment/realtime engine implementations.
- `cli/`: Python CLI package (`dalston_cli`) with its own tests.
- `sdk/`: Python SDK package (`dalston_sdk`) with its own tests.
- `web/`: React + TypeScript admin console.
- `docs/`, `infra/`, `docker/`, `alembic/`: architecture docs, Terraform, container files, and DB migrations.

## Build, Test, and Development Commands

- Prefer `make <target>` for common workflows; use raw commands only when no suitable Makefile target exists.
- `make help`: list available workflows.
- `make dev`: start full local stack (CPU-friendly).
- `make dev-minimal`: start minimal stack for quick iteration.
- `make dev-gpu`: start GPU stack (NVIDIA required).
- `make stop`: stop services.
- `make ps`: show running services.
- `make logs` / `make logs-all`: view service logs.
- `make health`: service health checks.
- `make validate`: validate compose configurations.
- `docker compose up -d`: start full local stack (gateway, workers, dependencies).
- `pip install -e ".[gateway,orchestrator,dev]"`: install backend for development.
- `pytest`: run default tests (`e2e` excluded by config).
- `pytest -m e2e`: run end-to-end tests (requires live Docker stack).
- `ruff check .` and `ruff format .`: lint and format Python code.
- `pre-commit run --all-files`: run all repository hooks.
- `npm run dev --prefix web`: run frontend locally.
- `npm run build --prefix web` and `npm run lint --prefix web`: build and lint frontend.

## Runtime Mode Guardrails

- Never run Docker and local Python processes for the same service simultaneously.
- Before Docker mode (`make dev`), stop local gateway/orchestrator processes:
  - `pkill -f "dalston.orchestrator" || true`
  - `pkill -f "dalston.gateway" || true`
  - `ps aux | grep -E "dalston\\.(orchestrator|gateway)" | grep -v grep`
- Before local mode, stop conflicting containers:
  - `docker compose stop orchestrator gateway`
- If debugging stuck jobs, check duplicate orchestrators/consumers:
  - `docker ps | grep orchestrator`
  - `ps aux | grep "dalston.orchestrator" | grep -v grep`
  - `docker compose exec redis redis-cli XINFO CONSUMERS "dalston:events:stream" orchestrators`

## Coding Style & Naming Conventions

- Python 3.11+, 4-space indentation, type hints encouraged.
- Ruff is authoritative for Python style (`line-length = 88`, import sorting enabled).
- Test files use `test_*.py`; keep new tests in the matching domain folder (`tests/unit`, `tests/integration`, etc.).
- For web code, follow ESLint + TypeScript rules; keep React components in PascalCase (example: `WebhookDetail.tsx`).

## Testing Guidelines

- Prefer focused unit tests first, then integration coverage for API/service boundaries.
- Use `@pytest.mark.e2e` for tests that require containers/external services.
- Add or update tests alongside behavior changes (backend, CLI, SDK, or web).

## Commit & Pull Request Guidelines

- Follow existing history style: short imperative subjects (examples: `Add ...`, `Fix ...`, `Implement M14: ...`).
- Keep commits scoped and atomic; avoid mixing refactors with feature changes.
- PRs should include: purpose, key changes, test evidence (commands run), and linked issue/milestone.
- Include screenshots or API examples when changing UI or external behavior.

## Security & Configuration Tips

- Copy from `.env.example`; never commit real secrets or API keys.
- Validate migrations in `alembic/versions/` and document config changes in `docs/` when relevant.

## API Modes

- Dalston-native: `/v1/audio/transcriptions/*`
- ElevenLabs-compatible: `/v1/speech-to-text/*`
- Realtime WS: `/v1/audio/transcriptions/stream` and `/v1/speech-to-text/realtime`

## Architecture Guardrails

- Keep handlers thin: parse request, call service, map response/errors.
- Keep business logic in service/domain layers, not API handlers.
- Prefer dependency injection over constructing external clients in core business logic.
- Avoid blocking I/O in async paths; use `asyncio.to_thread()` where unavoidable.
- Keep migrations append-only once released.
