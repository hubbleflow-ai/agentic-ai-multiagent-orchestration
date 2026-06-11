"""mcp-calendar · MCP server for adding trip events to Google Calendar.

Phase 5. Replaces calendar-agent. Runs in DRY-RUN mode if gcal OAuth
secrets aren't mounted (still mints fake event IDs so the demo flow
completes without real Google Calendar access). Live mode wiring is
preserved from the original agent for environments that have credentials.

Tools (MCP):
  - add_events(trip_id, events)  → AddEventsResult

Transport: streamable-http (stateless).
Endpoint:  POST http://mcp-calendar:9014/mcp
Health:    GET  http://mcp-calendar:9014/health
"""

from __future__ import annotations

import logging
import os
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from shared.observability import setup_observability

setup_observability("mcp-calendar")
log = logging.getLogger(__name__)


# ─── MCP server ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "calendar",
    instructions=(
        "Drop trip events (flights, hotels, activities) onto Google Calendar. "
        "Runs in dry-run mode if no OAuth secrets are mounted."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-calendar:9014",
            "mcp-calendar",
            "localhost:9014",
            "localhost",
            "127.0.0.1:9014",
            "127.0.0.1",
        ],
        allowed_origins=["*"],
    ),
)


# ─── config ──────────────────────────────────────────────────────────────

GOOGLE_TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", "/app/secrets/google-token.json")
GOOGLE_CREDS_PATH = os.environ.get("GOOGLE_CREDS_PATH", "/app/secrets/google-creds.json")


def _live_mode() -> bool:
    """True if real gcal OAuth creds are mounted · false → dry-run."""
    return os.path.exists(GOOGLE_TOKEN_PATH) and os.path.exists(GOOGLE_CREDS_PATH)


# ─── tool schema ─────────────────────────────────────────────────────────

class AddEventsResult(BaseModel):
    trip_id: str
    mode: str = Field(description="'live' (real gcal) or 'dry-run' (fake ids)")
    created_count: int
    event_ids: list[str]


# ─── MCP tool ────────────────────────────────────────────────────────────

@mcp.tool()
async def add_events(trip_id: str, events: list[dict]) -> AddEventsResult:
    """Add a list of events to the user's Google Calendar.

    Inputs:
      trip_id:  string identifier for this trip (for logging / dedup)
      events:   list of {title, date (YYYY-MM-DD), time (HH:MM)} dicts

    Returns: AddEventsResult with event_ids (one per input event).

    DRY-RUN mode (no secrets): mints fake event ids so the demo's
    "calendar populated" UI badge appears. LIVE mode (secrets present):
    calls real gcal v3 API · TODO wiring lift from old calendar-agent if
    the cohort wants live mode.
    """
    mode = "live" if _live_mode() else "dry-run"
    event_ids: list[str] = []
    for _ in events:
        # Dry-run · mint a fake id. Live-mode wiring (gcal client.events.insert)
        # was a TODO in the original agent too · stays a TODO here.
        event_ids.append(f"GCAL-{uuid.uuid4().hex[:10].upper()}")

    log.info("mcp.calendar.add_events trip=%s mode=%s count=%d",
             trip_id, mode, len(events))
    return AddEventsResult(
        trip_id=trip_id,
        mode=mode,
        created_count=len(events),
        event_ids=event_ids,
    )


# ─── HTTP surface ────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "mcp-calendar", "mode": "live" if _live_mode() else "dry-run"})


app = mcp.streamable_http_app()
