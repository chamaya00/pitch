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

# The PostgreSQL suites skip without TEST_DATABASE_URL. Point it at a scratch
# database to run them — they TRUNCATE between tests, so never at a real one.
#
# What skipping them costs was measured in Step 10.20: 185 tests, which is every
# test that touches SQL. A run without a database is worth having — it is fast
# and it catches most things — but it is not a run that has checked the
# repository's statements, and it should not be mistaken for one. A run that
# must not skip them sets REQUIRE_DATABASE_TESTS=1, which turns a missing DSN
# into a failure instead of 185 quiet skips; .github/workflows/checks.yml does.
if [ -n "${TEST_DATABASE_URL:-}" ]; then
  echo "==> backend: pytest (with PostgreSQL)"
else
  echo "==> backend: pytest (185 PostgreSQL tests will skip; set TEST_DATABASE_URL to run them)"
fi
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
