# ──────────────────────────────────────────────────────
# Run tests using docker compose (PostgreSQL + pytest).
# Usage:  .\scripts\run_tests.ps1
# ──────────────────────────────────────────────────────

Write-Host "🚀 Building and starting test environment..." -ForegroundColor Cyan
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from test-runner
$exitCode = $LASTEXITCODE

Write-Host "🧹 Cleaning up containers..." -ForegroundColor Cyan
docker compose -f docker-compose.test.yml down -v

exit $exitCode
