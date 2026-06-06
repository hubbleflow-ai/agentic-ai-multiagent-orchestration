#!/usr/bin/env bash
# Pre-flight check: verify every service is healthy before going on stage.
# Run this 20 min before the demo. If anything is red, fix it before the
# audience sees `docker compose up`.
#
# Usage: ./scripts/preflight.sh
# Exit: 0 if all healthy, 1 if any service is sick.

set -uo pipefail

services=(
  "redis:6379"               # tcp check
  "phoenix:6006/"            # phoenix UI
  "mock-airline-api:9001/health"
  "mock-hotel-api:9002/health"
  "mock-payment-api:9003/health"
  "flight-agent:8010/health"
  "hotel-agent:8011/health"
  "itinerary-agent:8012/health"
  "budget-agent:8013/health"
  "payment-agent:8014/health"
  "critic-agent:8015/health"
  "todo-agent:8016/health"
  "calendar-agent:8017/health"
  "research-agent:8018/health"
  "planner:8001/health"
  "concierge-voice:8000/health"
  "frontend:3000/"
)

fail=0

for s in "${services[@]}"; do
  name="${s%%:*}"
  rest="${s#*:}"
  port="${rest%%/*}"
  path="${rest#*/}"
  [[ "$path" == "$rest" ]] && path=""

  if [[ -z "$path" ]]; then
    # bare TCP check
    if nc -z localhost "$port" 2>/dev/null; then
      printf "  \033[32m✓\033[0m %-22s tcp/%s\n" "$name" "$port"
    else
      printf "  \033[31m✗\033[0m %-22s tcp/%s\n" "$name" "$port"
      fail=1
    fi
  else
    # HTTP check
    if curl -fsS -o /dev/null "http://localhost:${port}/${path}" 2>/dev/null; then
      printf "  \033[32m✓\033[0m %-22s http://localhost:%s/%s\n" "$name" "$port" "$path"
    else
      printf "  \033[31m✗\033[0m %-22s http://localhost:%s/%s\n" "$name" "$port" "$path"
      fail=1
    fi
  fi
done

echo
if [[ $fail -eq 0 ]]; then
  echo -e "\033[32mAll services healthy.\033[0m Ready to demo."
  exit 0
else
  echo -e "\033[31mOne or more services unhealthy.\033[0m Fix before going on stage."
  echo "  Try:  docker compose logs <service-name>"
  exit 1
fi
