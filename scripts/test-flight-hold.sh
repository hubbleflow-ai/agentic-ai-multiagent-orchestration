#!/usr/bin/env bash
# Diagnostic · sends a hold-only brief directly to flight-agent's A2A endpoint
# and inspects what artifacts come back. Answers the question:
#   "Is the hold problem in the flight-agent (no hold_flight artifact emitted)
#    or in the frontend (artifact arrives but isn't dispatched)?"
set -euo pipefail
cd "$(dirname "$0")/.."

curl -sS -X POST http://localhost:8010/ \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  --data @scripts/flight-hold-test.json | python3 -c "
import json, sys
resp = json.load(sys.stdin)
result = resp.get('result', {})
task = result.get('task') if 'task' in result else result
print('=== task state ===', task.get('status', {}).get('state', '?'))
print()
msg = task.get('status', {}).get('message', {})
text = ''.join(p.get('text','') for p in msg.get('parts', []))
print('=== agent final text (first 400 chars) ===')
print(text[:400])
print()
print('=== artifacts ===')
arts = task.get('artifacts', [])
print(f'count: {len(arts)}')
if not arts:
    print('  ⚠ NO ARTIFACTS · flight-agent did not call any MCP tool for this brief')
for a in arts:
    name = a.get('name', '?')
    print(f'  artifact name={name!r}')
    for p in a.get('parts', []):
        if 'data' in p:
            d = p['data']
            if isinstance(d, dict):
                r = d.get('result', d)
                if isinstance(r, list):
                    print(f'    data.result: list of {len(r)}')
                    if r and isinstance(r[0], dict):
                        keys = list(r[0].keys())
                        print(f'      first item keys: {keys}')
                        if 'hold_id' in keys:
                            print(f'      ✓ HAS hold_id={r[0].get(\"hold_id\")!r} flight_id={r[0].get(\"flight_id\")!r}')
                        else:
                            print(f'      first item sample: {json.dumps(r[0])[:200]}')
                else:
                    print(f'    data shape: {type(r).__name__}')
        elif 'text' in p:
            print(f'    text part: {p[\"text\"][:100]}')
"
