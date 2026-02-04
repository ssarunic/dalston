# Contributing to Dalston

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ssarunic/dalston.git
cd dalston

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[gateway,orchestrator,dev]"

# Install pre-commit hooks
pre-commit install

# Start Redis for tests
docker run -d -p 6379:6379 redis:7-alpine
```

## Before Submitting

1. **Run tests**: `pytest`
2. **Run linter**: `ruff check .`
3. **Format code**: `ruff format .`

Pre-commit hooks will run automatically on `git commit`.

## Pull Requests

- Create a branch from `main`
- Keep changes focused and atomic
- Update tests if adding new functionality
- Fill out the PR template

## Code Style

- Python 3.11+
- Formatted with [ruff](https://github.com/astral-sh/ruff)
- Type hints encouraged

## Questions?

Open an issue or start a discussion.
