#!/usr/bin/env bash
# Run every backend and frontend check. Use before committing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND_PY="$ROOT/backend/.venv/bin"
if [ ! -x "$BACKEND_PY/pytest" ]; then
  echo "Backend virtualenv not found at backend/.venv."
  echo "Create it with:"
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
  exit 1
fi

echo "==> backend: pytest"
(cd "$ROOT/backend" && "$BACKEND_PY/pytest")

echo "==> backend: ruff check"
(cd "$ROOT/backend" && "$BACKEND_PY/ruff" check .)

echo "==> backend: ruff format --check"
(cd "$ROOT/backend" && "$BACKEND_PY/ruff" format --check .)

echo "==> backend: mypy"
(cd "$ROOT/backend" && "$BACKEND_PY/mypy" app)

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Frontend dependencies not installed. Run: cd frontend && npm install"
  exit 1
fi

echo "==> frontend: eslint"
(cd "$ROOT/frontend" && npm run --silent lint)

echo "==> frontend: tsc --noEmit"
(cd "$ROOT/frontend" && npm run --silent typecheck)

echo "==> frontend: node --test"
(cd "$ROOT/frontend" && npm run --silent test >/dev/null)

echo "==> frontend: next build"
(cd "$ROOT/frontend" && npm run --silent build >/dev/null)

echo "All checks passed."
