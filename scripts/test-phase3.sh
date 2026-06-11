#!/usr/bin/env bash
# Phase 3 verification · sends one trip-planning request to the planner
# and streams the SSE events. Look for:
#   - tool.started   {name: "delegate_to_flight_agent", args: {brief: "..."}}
#   - tool.finished  {name: "delegate_to_flight_agent", result: "<sub-agent's reply>"}
set -euo pipefail

cd "$(dirname "$0")/.."

curl -N -X POST http://localhost:8001/agent/stream \
  -H "Content-Type: application/json" \
  --data @scripts/phase3-test.json
