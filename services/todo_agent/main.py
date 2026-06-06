"""todo-agent · A2A worker for user-facing pre/in/post-trip todos.

REUSED FROM SESSION 4 (conceptually). The S4 implementation was a stateful
LangGraph agent maintaining its own todo list in Redis. Here we A2A-wrap a
simpler version: given a high-level brief, generate the right todos with
priorities and due dates.

A2A skills:
  - create_todos(trip_brief, destination, depart_date) →
      {todos: [{text, priority, due_date}, ...]}

Stored in Redis at todos:<trip_id> so the frontend can render them.

Phase 3 will swap in an LLM-driven todo generator that adapts based on the
specific destination (passport renewal for international, scooter license
for Bali, etc.).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

import redis.asyncio as redis
from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("todo-agent")

app = FastAPI(title="todo-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="todo-agent",
    description="Creates pre/in/post-trip todos with priorities and due dates.",
)

_r = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "todo-agent"}


# Standard pre-trip todo templates. Phase 3 LLM picks/adapts these per trip.
_DEFAULT_TODOS: list[dict] = [
    {"text": "Renew passport if expiring within 6 months", "priority": "high",   "offset_days": -45},
    {"text": "Apply for visa / e-Visa",                    "priority": "high",   "offset_days": -30},
    {"text": "Buy travel insurance",                       "priority": "medium", "offset_days": -14},
    {"text": "Notify bank of international travel",        "priority": "medium", "offset_days": -7},
    {"text": "Exchange / load forex card",                 "priority": "medium", "offset_days": -5},
    {"text": "Pack camera + adapters + power bank",        "priority": "low",    "offset_days": -2},
    {"text": "Online check-in 24h before flight",          "priority": "medium", "offset_days": -1},
]


@a2a.skill("create_todos")
async def create_todos(payload: dict) -> dict:
    trip_id = payload["trip_id"]
    depart_str = payload.get("depart_date", "2026-10-15")
    try:
        depart = date.fromisoformat(depart_str)
    except ValueError:
        depart = date.today() + timedelta(days=60)

    todos = []
    for i, t in enumerate(_DEFAULT_TODOS):
        due = depart + timedelta(days=t["offset_days"])
        todos.append({
            "id": i + 1,
            "text": t["text"],
            "priority": t["priority"],
            "due_date": due.isoformat(),
        })

    await _r.set(f"todos:{trip_id}", json.dumps(todos))
    return {"trip_id": trip_id, "count": len(todos), "todos": todos}
