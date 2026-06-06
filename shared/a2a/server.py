"""A2A server · turns FastAPI endpoints into A2A-compliant skill endpoints.

Decorator pattern: each worker declares its skills with `@a2a.skill("name")`,
which registers a POST /a2a/<name> route that takes a JSON payload and returns
the handler's dict result.

Also publishes an Agent Card at GET /.well-known/agent.json so callers can
discover what the agent can do (a lightweight version of the spec).

Usage in a worker service:
    from fastapi import FastAPI
    from shared.a2a import A2AServer

    app = FastAPI()
    a2a = A2AServer(app, agent_name="flight-agent",
                    description="Searches and holds flights.")

    @a2a.skill("search_flights")
    async def search_flights(payload: dict) -> dict:
        return {"options": [...]}
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request

log = logging.getLogger(__name__)


SkillHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class A2AServer:
    def __init__(
        self,
        app: FastAPI,
        agent_name: str,
        description: str = "",
    ) -> None:
        self.app = app
        self.agent_name = agent_name
        self.description = description
        self._handlers: dict[str, SkillHandler] = {}

        # Agent Card — minimal but useful for discovery.
        @app.get("/.well-known/agent.json")
        def agent_card() -> dict[str, Any]:
            return {
                "name": agent_name,
                "description": description,
                "skills": [
                    {"name": s, "endpoint": f"/a2a/{s}"} for s in self._handlers
                ],
                "protocol": "a2a/0.1-lite",
            }

    def skill(self, name: str) -> Callable[[SkillHandler], SkillHandler]:
        """Decorator: register `func` as the handler for skill `name`.

        Wires POST /a2a/<name> that calls the handler with the parsed
        request JSON and returns its dict result.
        """

        def decorator(func: SkillHandler) -> SkillHandler:
            if name in self._handlers:
                raise ValueError(f"A2A skill '{name}' already registered on {self.agent_name}")
            self._handlers[name] = func

            async def endpoint(req: Request) -> dict[str, Any]:
                # Tolerate empty body (treat as {}).
                try:
                    payload = await req.json()
                except Exception:
                    payload = {}
                log.info("a2a.in agent=%s skill=%s", self.agent_name, name)
                result = await func(payload)
                log.info("a2a.out agent=%s skill=%s keys=%s",
                         self.agent_name, name, list(result.keys()) if isinstance(result, dict) else "?")
                return result

            self.app.post(f"/a2a/{name}")(endpoint)
            return func

        return decorator
