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

- `docker compose up -d`: start full local stack (gateway, workers, dependencies).
- `pip install -e ".[gateway,orchestrator,dev]"`: install backend for development.
- `pytest`: run default tests (`e2e` excluded by config).
- `pytest -m e2e`: run end-to-end tests (requires live Docker stack).
- `ruff check .` and `ruff format .`: lint and format Python code.
- `pre-commit run --all-files`: run all repository hooks.
- `npm run dev --prefix web`: run frontend locally.
- `npm run build --prefix web` and `npm run lint --prefix web`: build and lint frontend.

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
