"""Redis-backed plan store · the Concierge's Plan lives here.

Why Redis (not Planner local memory):
  - Workers can self-report status (loose coupling)
  - Plan survives Planner restart (resumability)
  - Frontend subscribes to live updates via pub/sub → SSE
  - Multiple watchers possible without coordination

Schema:
  KEY    plan:<trip_id>                 · JSON-encoded list[PlanStep]
  CHAN   plan:<trip_id>:updates         · publishes JSON {trip_id, plan}

Trip IDs are short opaque strings (uuid4 hex). One Concierge run = one trip.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import AsyncIterator

import redis.asyncio as redis

log = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class PlanStep:
    id: int
    task: str
    agent: str
    status: StepStatus = StepStatus.PENDING
    result_ref: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PlanStep":
        return cls(
            id=d["id"],
            task=d["task"],
            agent=d["agent"],
            status=StepStatus(d.get("status", "pending")),
            result_ref=d.get("result_ref"),
        )


def _plan_key(trip_id: str) -> str:
    return f"plan:{trip_id}"


def _channel(trip_id: str) -> str:
    return f"plan:{trip_id}:updates"


class PlanStore:
    """Async Redis-backed plan store + pub/sub."""

    def __init__(self, redis_url: str) -> None:
        # decode_responses=True so we get strings, not bytes.
        self._r = redis.from_url(redis_url, decode_responses=True)

    async def aclose(self) -> None:
        await self._r.aclose()

    async def create_plan(self, trip_id: str, steps: list[PlanStep]) -> None:
        """Write the initial plan and publish a create event."""
        payload = [s.to_dict() for s in steps]
        await self._r.set(_plan_key(trip_id), json.dumps(payload))
        await self._publish(trip_id, payload)

    async def get_plan(self, trip_id: str) -> list[PlanStep]:
        raw = await self._r.get(_plan_key(trip_id))
        if not raw:
            return []
        return [PlanStep.from_dict(d) for d in json.loads(raw)]

    async def update_step(
        self,
        trip_id: str,
        step_id: int,
        status: StepStatus,
        result_ref: str | None = None,
    ) -> None:
        """Mark a step's status (and optional result_ref). Publishes an update."""
        plan = await self.get_plan(trip_id)
        for step in plan:
            if step.id == step_id:
                step.status = status
                if result_ref is not None:
                    step.result_ref = result_ref
                break
        else:
            log.warning("plan_state.update_step.step_not_found trip=%s step=%d", trip_id, step_id)
            return
        payload = [s.to_dict() for s in plan]
        await self._r.set(_plan_key(trip_id), json.dumps(payload))
        await self._publish(trip_id, payload)

    async def insert_step(
        self,
        trip_id: str,
        after_step_id: int,
        new_step: PlanStep,
    ) -> None:
        """Insert a step into the plan after `after_step_id`. Used by Planner
        when CriticAgent flags an issue and a revision step is needed."""
        plan = await self.get_plan(trip_id)
        out: list[PlanStep] = []
        for step in plan:
            out.append(step)
            if step.id == after_step_id:
                out.append(new_step)
        payload = [s.to_dict() for s in out]
        await self._r.set(_plan_key(trip_id), json.dumps(payload))
        await self._publish(trip_id, payload)

    async def _publish(self, trip_id: str, payload: list[dict]) -> None:
        message = json.dumps({"trip_id": trip_id, "plan": payload})
        await self._r.publish(_channel(trip_id), message)

    async def subscribe(self, trip_id: str) -> AsyncIterator[dict]:
        """Async iterator yielding each `{trip_id, plan}` event.

        Used by Planner's SSE endpoint to relay updates to the frontend.
        """
        pubsub = self._r.pubsub()
        await pubsub.subscribe(_channel(trip_id))
        try:
            # First, send the current state (so a late subscriber gets the
            # snapshot, not just future updates).
            current = await self.get_plan(trip_id)
            if current:
                yield {"trip_id": trip_id, "plan": [s.to_dict() for s in current]}

            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    log.warning("plan_state.subscribe.bad_payload trip=%s", trip_id)
        finally:
            await pubsub.unsubscribe(_channel(trip_id))
            await pubsub.aclose()

    async def subscribe_all(self) -> AsyncIterator[dict]:
        """Async iterator over plan updates across ALL trip IDs.

        Used by the frontend SSE when no specific trip_id is known (it sees
        whatever trip is currently active). Uses Redis pattern subscribe.
        """
        pubsub = self._r.pubsub()
        await pubsub.psubscribe("plan:*:updates")
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "pmessage":
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
        finally:
            await pubsub.punsubscribe("plan:*:updates")
            await pubsub.aclose()
