#!/usr/bin/env bash
# First-time setup. Copies env template, reminds about secrets, sanity-checks
# docker availability.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.template .env
  echo "✓ Created .env from template. Fill in GOOGLE_API_KEY before continuing."
else
  echo "  .env already exists."
fi

mkdir -p secrets
touch secrets/.gitkeep
echo "✓ secrets/ directory ready."

if [[ ! -f secrets/google-token.json || ! -f secrets/google-creds.json ]]; then
  echo
  echo "  ⚠ Calendar agent needs google-token.json + google-creds.json in secrets/."
  echo "    Generate them via:"
  echo "      cd ../agentic-ai-introduction/backend"
  echo "      uv run python scripts/bootstrap_gcal_token.py"
  echo "    Then copy the resulting tokens into agentic-ai-multiagent-orchestration/secrets/."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo
  echo "✗ docker not found. Install Docker Desktop before continuing."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo
  echo "✗ docker compose v2 not available. Update Docker Desktop."
  exit 1
fi

echo
echo "Next:"
echo "  1. Edit .env and set GOOGLE_API_KEY"
echo "  2. (Optional) Drop google-token.json + google-creds.json in secrets/"
echo "  3. docker compose up --build"
echo "  4. ./scripts/preflight.sh"
echo "  5. open http://localhost:3000"
