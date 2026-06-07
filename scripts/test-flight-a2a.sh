#!/usr/bin/env bash
# Phase 2 verification · sends one A2A message/send to flight-agent and
# pretty-prints the JSON-RPC response.
set -euo pipefail

cd "$(dirname "$0")/.."

curl -sS -X POST http://localhost:8010/ \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  --data @scripts/a2a-flight-test.json \
  | python3 -m json.tool
