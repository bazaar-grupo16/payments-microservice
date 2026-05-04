#!/usr/bin/env bash
# ──────────────────────────────────────────────────────
# Run tests using docker compose (PostgreSQL + pytest).
# Usage:  ./scripts/run_tests.sh
# ──────────────────────────────────────────────────────
set -euo pipefail

echo "🚀 Building and starting test environment..."
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from test-runner
EXIT_CODE=$?

echo "🧹 Cleaning up containers..."
docker compose -f docker-compose.test.yml down -v

exit $EXIT_CODE
