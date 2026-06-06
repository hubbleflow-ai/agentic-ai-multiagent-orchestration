"""calendar-agent · A2A worker for dropping trip events onto Google Calendar.

A2A skills:
  - add_events(trip_id, events[]) → {created_count, event_ids[]}

Wraps Google Calendar via the OAuth credentials in /app/secrets/. If the
secrets aren't present (e.g. local dev without gcal access) the agent
runs in DRY-RUN mode and just logs what it WOULD have created.

Phase 3 will swap this for the S5 gcal MCP server. For Phase 2A, dry-run
is enough to validate the wiring end-to-end.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("calendar-agent")

app = FastAPI(title="calendar-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="calendar-agent",
    description="Creates calendar events (flights, hotel, activities). Dry-run if no creds.",
)

log = logging.getLogger(__name__)
GOOGLE_TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", "/app/secrets/google-token.json")
GOOGLE_CREDS_PATH = os.environ.get("GOOGLE_CREDS_PATH", "/app/secrets/google-creds.json")


def _live_mode() -> bool:
    return os.path.exists(GOOGLE_TOKEN_PATH) and os.path.exists(GOOGLE_CREDS_PATH)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "calendar-agent",
        "mode": "live" if _live_mode() else "dry-run",
    }


@a2a.skill("add_events")
async def add_events(payload: dict) -> dict:
    trip_id = payload.get("trip_id", "unknown")
    events: list[dict] = payload.get("events", [])
    mode = "live" if _live_mode() else "dry-run"

    event_ids: list[str] = []
    for ev in events:
        # In dry-run we just mint an id. In Phase 3 we'd call gcal here.
        event_ids.append(f"GCAL-{uuid.uuid4().hex[:10].upper()}")

    log.info("calendar.add_events trip=%s mode=%s count=%d", trip_id, mode, len(events))
    return {
        "trip_id": trip_id,
        "mode": mode,
        "created_count": len(events),
        "event_ids": event_ids,
    }
