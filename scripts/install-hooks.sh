#!/bin/bash
# Install git hooks for Dalston development
# Run: ./scripts/install-hooks.sh

HOOKS_DIR=".git/hooks"

# Pre-push hook
cat > "$HOOKS_DIR/pre-push" << 'EOF'
#!/bin/bash
# Pre-push hook: runs linter and tests before pushing
# Skip with: git push --no-verify

echo "Running pre-push checks..."

# Run linter
echo "→ Checking linter..."
if ! ruff check . --quiet; then
    echo "❌ Linter failed. Fix errors before pushing."
    exit 1
fi

# Run tests
echo "→ Running tests..."
if ! pytest --quiet --tb=line 2>/dev/null; then
    echo "❌ Tests failed. Fix failures before pushing."
    exit 1
fi

echo "✓ All checks passed!"
EOF

chmod +x "$HOOKS_DIR/pre-push"
echo "✓ Pre-push hook installed"
