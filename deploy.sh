#!/usr/bin/env bash
# Server-side deploy: pull latest main, rebuild, restart, health-check.
# Run by CI (GitHub Actions) over SSH on push to main, or manually: `bash deploy.sh`.
set -euo pipefail
cd "$(dirname "$0")"

TOKEN=$(grep -m1 '^GITHUB_TOKEN=' "$HOME/.hermes/.env" | cut -d= -f2- | tr -d '"' | tr -d '\r')
REPO="https://x-access-token:${TOKEN}@github.com/Lora-git-hub/darling-admin-panel.git"

echo "[deploy] fetching origin/main…"
git fetch -q "$REPO" main
git reset --hard FETCH_HEAD

echo "[deploy] building frontend…"
npm --prefix frontend ci --silent
npm --prefix frontend run build

echo "[deploy] rebuilding + restarting containers…"
docker compose up -d --build
docker image prune -f >/dev/null 2>&1 || true

echo "[deploy] health check…"
sleep 6
if curl -fsS https://panel.darlinghome.bg/api/health >/dev/null; then
  echo "[deploy] OK — now at $(git rev-parse --short HEAD)"
else
  echo "[deploy] HEALTH CHECK FAILED"; exit 1
fi
