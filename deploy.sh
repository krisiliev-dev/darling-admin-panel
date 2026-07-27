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
# The panel's Python is bind-mounted (..:/app), NOT baked into the image, and uvicorn runs without
# --reload. So when requirements are unchanged the built image is cache-identical and `up` leaves the
# OLD container — and old code — running: the deploy looks healthy but never loaded the new code.
# Force-recreate the panel service so the new bind-mounted code is actually picked up. Caddy is left
# alone (--no-deps → no needless TLS-proxy bounce); the SPA is already live from the dist rebuild above,
# which Caddy serves straight from disk.
docker compose up -d --force-recreate --no-deps panel
docker image prune -f >/dev/null 2>&1 || true

echo "[deploy] health check…"
# Poll rather than a flat sleep: the container needs a variable moment to boot
# (alembic migrate + uvicorn) and a fixed wait races startup → false 502 failures.
for i in $(seq 1 20); do
  if curl -fsS --max-time 5 https://panel.darlinghome.bg/api/health >/dev/null; then
    echo "[deploy] OK — now at $(git rev-parse --short HEAD) (healthy after ~$((i*3))s)"
    exit 0
  fi
  sleep 3
done
echo "[deploy] HEALTH CHECK FAILED — not healthy after ~60s"; exit 1
