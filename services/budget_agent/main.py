"""budget-agent · A2A cost gate, Redis-backed.

A2A skills:
  - check_budget(trip_id, proposed_amount_inr, category)
      → {ok, remaining_inr, reason?}
  - commit_spend(trip_id, amount_inr, category, description)
      → {ok, new_remaining_inr, total_spent_inr}
  - set_budget(trip_id, limit_inr) → {ok, limit_inr}

State lives in Redis at `budget:<trip_id>` so survives Planner restarts.

Distinct from PaymentAgent: BudgetAgent is the SILENT cost-gate (returns
ok=false if over budget). PaymentAgent is the LOUD human-in-the-loop
approval gate (interrupts execution to ask the user via voice).
"""

from __future__ import annotations

import json
import logging
import os

import redis.asyncio as redis
from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("budget-agent")

app = FastAPI(title="budget-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="budget-agent",
    description="Tracks a trip's running spend against a budget ceiling.",
)

log = logging.getLogger(__name__)
_r = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


def _key(trip_id: str) -> str:
    return f"budget:{trip_id}"


async def _load(trip_id: str) -> dict:
    raw = await _r.get(_key(trip_id))
    if raw:
        return json.loads(raw)
    return {"limit_inr": 200_000, "spent_inr": 0, "categories": {}}


async def _save(trip_id: str, data: dict) -> None:
    await _r.set(_key(trip_id), json.dumps(data))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "budget-agent"}


@a2a.skill("set_budget")
async def set_budget(payload: dict) -> dict:
    trip_id = payload["trip_id"]
    limit_inr = int(payload["limit_inr"])
    data = {"limit_inr": limit_inr, "spent_inr": 0, "categories": {}}
    await _save(trip_id, data)
    return {"ok": True, "limit_inr": limit_inr, "remaining_inr": limit_inr}


@a2a.skill("check_budget")
async def check_budget(payload: dict) -> dict:
    trip_id = payload["trip_id"]
    proposed = int(payload["proposed_amount_inr"])
    category = payload.get("category", "uncategorized")
    data = await _load(trip_id)
    remaining = data["limit_inr"] - data["spent_inr"]
    ok = proposed <= remaining
    return {
        "ok": ok,
        "remaining_inr": remaining,
        "limit_inr": data["limit_inr"],
        "spent_inr": data["spent_inr"],
        "category": category,
        "proposed": proposed,
        "reason": None if ok else f"₹{proposed} exceeds remaining ₹{remaining}",
    }


@a2a.skill("commit_spend")
async def commit_spend(payload: dict) -> dict:
    trip_id = payload["trip_id"]
    amount = int(payload["amount_inr"])
    category = payload.get("category", "uncategorized")
    description = payload.get("description", "")
    data = await _load(trip_id)
    data["spent_inr"] += amount
    data["categories"][category] = data["categories"].get(category, 0) + amount
    await _save(trip_id, data)
    log.info("budget.commit trip=%s amount=%d cat=%s desc=%s spent=%d",
             trip_id, amount, category, description, data["spent_inr"])
    return {
        "ok": True,
        "new_remaining_inr": data["limit_inr"] - data["spent_inr"],
        "total_spent_inr": data["spent_inr"],
        "categories": data["categories"],
    }
