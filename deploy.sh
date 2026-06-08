#!/usr/bin/env bash
#
# deploy.sh - Self-contained deploy for the WC2026 prediction bot.
#
# Standalone: this project is NOT part of the homeserver orchestrator. It runs
# its own docker-compose stack with its own named volume. Run this on the
# server after pushing to GitHub.
#
# Usage: bash deploy.sh

set -euo pipefail
cd "$(dirname "$0")"

echo "=== WC2026 Bot Deploy ==="

# .env must exist (created manually on the server; never committed).
if [ ! -f .env ]; then
  echo "❌ .env not found. Create it from .env.example before deploying."
  exit 1
fi

echo "[1/3] Syncing to origin/main..."
git fetch origin
git reset --hard origin/main

echo "[2/3] Rebuilding and restarting..."
docker compose up -d --build

echo "[3/3] Waiting for the container to settle..."
sleep 5
running=$(docker inspect --format='{{.State.Running}}' wc2026bot 2>/dev/null || echo "false")
docker compose ps

if [ "$running" = "true" ]; then
  echo ""
  echo "✅ Deploy concluído — wc2026bot a correr."
  echo "   Logs: docker compose logs -f wc2026bot"
else
  echo ""
  echo "⚠️  wc2026bot não está a correr — logs:"
  docker compose logs --tail=40 wc2026bot
  exit 1
fi
