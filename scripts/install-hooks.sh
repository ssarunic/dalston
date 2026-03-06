#!/bin/bash
# Install git hooks for Dalston development
# Run: ./scripts/install-hooks.sh

set -euo pipefail

HOOKS_DIR=".git/hooks"

# Pre-push hook
cat > "$HOOKS_DIR/pre-push" << 'EOF'
#!/bin/bash
# Pre-push hook: runs linter and tests before pushing
# Skip with: git push --no-verify

set -euo pipefail

echo "Running pre-push checks..."

if command -v uv >/dev/null 2>&1; then
    RUFF_CMD=(uv run --python 3.11 ruff check . --quiet)
    TEST_CMD=(uv run --python 3.11 pytest --quiet --tb=line)
else
    PYTHON_BIN=".venv/bin/python"
    if [ ! -x "$PYTHON_BIN" ]; then
        echo "❌ Missing Python runner. Install uv or create .venv with Python 3.11."
        echo "Example: uv venv --python 3.11"
        exit 1
    fi

    if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        echo "❌ .venv is using Python < 3.11."
        echo "Recreate it with Python 3.11 (example: rm -rf .venv && uv venv --python 3.11)."
        exit 1
    fi

    RUFF_CMD=("$PYTHON_BIN" -m ruff check . --quiet)
    TEST_CMD=("$PYTHON_BIN" -m pytest --quiet --tb=line)
fi

# Normalize local environment for test determinism.
# Some unit tests assume S3/AWS env vars are unset unless explicitly provided.
TEST_ENV=(
    env
    -u DALSTON_S3_BUCKET
    -u DALSTON_S3_ENDPOINT_URL
    -u DALSTON_S3_PUBLIC_ENDPOINT_URL
    -u DALSTON_MODEL_CACHE_DIR
    -u AWS_ACCESS_KEY_ID
    -u AWS_SECRET_ACCESS_KEY
    -u AWS_SESSION_TOKEN
    -u AWS_PROFILE
    -u AWS_DEFAULT_PROFILE
    -u AWS_REGION
    -u AWS_DEFAULT_REGION
    PYTHON_DOTENV_DISABLED=1
)

echo "→ Checking linter..."
if ! "${RUFF_CMD[@]}"; then
    echo "❌ Linter failed. Fix errors before pushing."
    exit 1
fi

echo "→ Running tests..."
if ! "${TEST_ENV[@]}" "${TEST_CMD[@]}" 2>/dev/null; then
    echo "❌ Tests failed. Fix failures before pushing."
    exit 1
fi

echo "✓ All checks passed!"
EOF

chmod +x "$HOOKS_DIR/pre-push"
echo "✓ Pre-push hook installed"
