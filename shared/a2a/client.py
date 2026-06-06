"""A2A client · the Planner uses this to call worker services.

Minimal HTTP-based A2A: peer exposes POST /a2a/<skill> taking a JSON payload,
returns JSON. Compatible enough with Google's A2A spec for cohort purposes
without dragging in the full SDK. Wire format can be upgraded to spec-strict
JSON-RPC envelopes in a later pass.

Usage in Planner:
    flight = A2AClient(os.environ["FLIGHT_AGENT_URL"])
    result = await flight.invoke("search_flights", {"origin": "BLR", ...})
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class A2AClientError(RuntimeError):
    """Raised when an A2A peer call fails."""


class A2AClient:
    # Default timeout is generous because some workers do LLM generation
    # on cache-miss (flight_agent + hotel_agent generate 12 options via
    # Gemini, which can take 30-90s for the first call on a new route).
    # Subsequent calls hit Redis cache and return in <10ms.
    def __init__(self, peer_url: str, timeout: float = 180.0) -> None:
        self.peer_url = peer_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def invoke(self, skill: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call `skill` on the peer with `payload`. Returns the result dict.

        Raises A2AClientError on transport or non-2xx response.
        """
        url = f"{self.peer_url}/a2a/{skill}"
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.HTTPError as e:
            log.warning("a2a.transport_error url=%s err=%s", url, e)
            raise A2AClientError(f"transport error calling {url}: {e}") from e

        if resp.status_code >= 400:
            body = resp.text[:500]
            log.warning("a2a.bad_status url=%s status=%s body=%s", url, resp.status_code, body)
            raise A2AClientError(f"{url} returned {resp.status_code}: {body}")

        try:
            return resp.json()
        except ValueError as e:
            raise A2AClientError(f"{url} returned non-JSON: {resp.text[:200]}") from e

    async def aclose(self) -> None:
        await self._client.aclose()
