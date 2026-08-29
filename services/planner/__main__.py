"""planner · debug entry point. Run the supervisor on your host, in a debugger.

    uv run python -m services.planner

Why this is a separate module rather than an ``if __name__ == "__main__"``
block in ``main.py``:

``main.py`` imports ``services.planner.graph`` at line 38, and *that* module
calls ``build_all_a2a_tools()`` at import time — six synchronous Agent Card
fetches, before any function body runs. Inside Docker those URLs are service
names (``http://flight-agent:8010/``) which do not resolve on your laptop, so
the import raises and the process dies. A ``__main__`` block at the bottom of
``main.py`` would never be reached: the crash happens on the way in.

So the env has to be fixed up *before* the first planner import, which means
it has to live in a module that runs first. ``python -m services.planner``
executes this file, and the import of ``main`` below is the first thing that
touches ``config.py``.

Everything uses ``setdefault``, so this file is inert inside Docker where
compose already supplies the real values.

Notes for a debugging session:

  - **Port.** Defaults to 8002 because the planner *container* holds 8001.
    Both can run at once; they are separate processes sharing the same
    sub-agents. Override with ``PORT``.
  - **Reload is off**, deliberately. Uvicorn's reloader runs the app in a
    child process while the debugger stays attached to the parent, so
    breakpoints silently never fire.
  - **A2A delegation is patched, see ``_patch_a2a_for_localhost`` below.**
    The env overrides fix where each Agent Card is *fetched from*, but the
    a2a-sdk then sends the task to the URL the card itself advertises —
    ``http://flight-agent:8010/`` — which does not resolve on a laptop.
    MCP tools are unaffected (plain HTTP to the URLs above); only
    ``delegate_to_*`` would fail, with
    ``A2AClientError: nodename nor servname provided``.

  - **The frontend will not reach this process.** It talks to
    ``concierge-voice``, which reaches the planner over Docker DNS. Drive
    this one directly instead::

        curl -N -X POST http://localhost:8002/agent/stream \\
          -H 'Content-Type: application/json' \\
          -d '{"session_id":"debug-1","message":"Plan a trip to Tokyo"}'

  - The mesh must be up (``docker compose up -d``); this process is a
    *client* of the sub-agents and MCP servers, not a replacement for them.
"""

from __future__ import annotations

import os

# Host-mapped equivalents of the in-network addresses in config.py. These
# ports come from docker-compose (plus docker-compose.override.yml, which
# remaps redis 6379 -> 6380 so it can coexist with the Session 4/5 stack).
_HOST_DEFAULTS = {
    # A2A sub-agents
    "FLIGHT_AGENT_A2A_URL":    "http://localhost:8010/",
    "HOTEL_AGENT_A2A_URL":     "http://localhost:8011/",
    "ITINERARY_AGENT_A2A_URL": "http://localhost:8012/",
    "CRITIC_AGENT_A2A_URL":    "http://localhost:8015/",
    "TODO_AGENT_A2A_URL":      "http://localhost:8016/",
    "RESEARCH_AGENT_A2A_URL":  "http://localhost:8018/",
    # MCP servers the planner calls directly
    "MCP_TRIP_STATE_URL":      "http://localhost:9015/mcp",
    "MCP_PAYMENT_URL":         "http://localhost:9013/mcp",
    "MCP_CALENDAR_URL":        "http://localhost:9014/mcp",
    # state
    "REDIS_URL":               "redis://localhost:6380/0",
}

for _key, _value in _HOST_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

# Load .env if present so GOOGLE_API_KEY is picked up the same way the
# containers get it from compose's env_file.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover · python-dotenv is a declared dep
    pass

def _patch_a2a_for_localhost() -> None:
    """Make the a2a-sdk send to the address we can reach, not the advertised one.

    ``create_client(url)`` resolves the Agent Card and then honours
    ``card.supported_interfaces[0].url``. That URL is the agent's *in-network*
    address, so from the host the send fails even though the card fetch
    succeeded. Overriding the env is not enough — the SDK discards our URL in
    favour of the card's.

    ``create_client`` also accepts an ``AgentCard`` directly, so we fetch the
    card ourselves, rewrite the advertised URL to the reachable one, and hand
    the SDK a card that points somewhere real.

    Note the card JSON is camelCase (``supportedInterfaces``) while the proto
    field is snake_case, so ``AgentCard(**raw)`` raises. ``ParseDict`` does the
    conversion.

    Debug-only: nothing calls this inside Docker, where the advertised URL is
    already correct.
    """
    import httpx
    from a2a.client import ClientConfig, create_client
    from a2a.types import AgentCard
    from google.protobuf.json_format import ParseDict

    from shared.a2a_helpers import client as a2a_client

    async def _get_client_localhost(agent_url: str):
        if agent_url not in a2a_client._clients:
            raw = httpx.get(
                agent_url.rstrip("/") + "/.well-known/agent-card.json", timeout=10.0
            ).json()
            for iface in raw.get("supportedInterfaces", []):
                iface["url"] = agent_url
            card = ParseDict(raw, AgentCard())
            a2a_client._clients[agent_url] = await create_client(
                card, client_config=ClientConfig(httpx_client=a2a_client._get_httpx_client())
            )
        return a2a_client._clients[agent_url]

    a2a_client._get_client = _get_client_localhost


_patch_a2a_for_localhost()

# Import AFTER the env is settled · this is the line that triggers the six
# Agent Card fetches described above.
from services.planner.main import app  # noqa: E402


def main() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8001"))
    print(f"planner (debug) · http://127.0.0.1:{port}")
    print(f"  A2A sub-agents : {_HOST_DEFAULTS['FLIGHT_AGENT_A2A_URL']} (and 5 more)")
    print(f"  redis          : {os.environ['REDIS_URL']}")
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=port,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
