"""mcp-airline · MCP server exposing flight inventory tools.

This is a non-reasoning service: it generates realistic flight options
(via Gemini structured output, cached in Redis) and lets callers place
holds. There is no "agent" here · just tools an upstream agent can call.

Tools (MCP):
  - search_flights(origin, destination, depart_date, pax, return_date?)
      → list[FlightOption]   (12 options, sorted by total price ascending)
  - hold_flight(flight_id, pax) → HoldResult   (30-minute hold)

Transport: streamable-http (stateless + JSON response mode).
Endpoint:  POST http://mcp-airline:9011/mcp     (JSON-RPC 2.0, no trailing slash)
Health:    GET  http://mcp-airline:9011/health

The Gemini option-generation logic was lifted verbatim from the previous
`services/flight_agent/main.py` · per the Session 6 refactor plan, the
*agent* layer (Phase 2) reasons over these options, the *MCP* layer
provides the raw inventory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from shared.observability import setup_observability

setup_observability("mcp-airline")

log = logging.getLogger(__name__)


# ─── MCP server ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "airline",
    instructions=(
        "Realistic flight-inventory tools for an Indian travel concierge. "
        "Call search_flights to get 12 options for a route; call hold_flight "
        "to place a 30-minute hold on one of them."
    ),
    # stateless_http=True · no session storage, horizontal-scale friendly.
    stateless_http=True,
    # FastMCP enables DNS-rebinding protection by default (only accepts
    # Host: localhost). Inside our Docker bridge network the planner and
    # sub-agents talk to mcp servers by service DNS name (e.g.
    # mcp-airline:9011) so we explicitly allowlist those hosts.
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-airline:9011",
            "mcp-airline",
            "localhost:9011",
            "localhost",
            "127.0.0.1:9011",
            "127.0.0.1",
        ],
        allowed_origins=["*"],
    ),
)


# ─── tool schemas (Pydantic → JSON schema seen by MCP clients) ───────────

class FlightOption(BaseModel):
    flight_id: str = Field(description="Airline code + number, e.g. 'AI314' or '6E1503'")
    airline: str = Field(description="Full airline name, e.g. 'Air India', 'IndiGo'")
    alliance: str = Field(description="'Star Alliance', 'Oneworld', 'SkyTeam', or 'no alliance'")
    origin: str = Field(description="3-letter IATA airport code")
    destination: str = Field(description="3-letter IATA airport code")
    depart_date: str = Field(description="YYYY-MM-DD")
    return_date: Optional[str] = Field(None, description="YYYY-MM-DD or null for one-way")
    dep_time: str = Field(description="HH:MM 24-hour")
    arr_time: str = Field(description="HH:MM 24-hour, append ' (+1 day)' if next-day")
    duration_hours: float = Field(description="Total flight duration in hours, e.g. 10.5")
    stops: int = Field(ge=0, le=3, description="Number of stops, 0 for non-stop")
    price_per_pax_inr: int = Field(description="Price per passenger in INR")
    price_total_inr: int = Field(
        description="Total price = per_pax * pax * (2 if round-trip, 1 if one-way)"
    )
    pax: int = Field(description="Number of passengers in this booking")


class _StructuredFlightOptions(BaseModel):
    """Internal-only · used to constrain Gemini's response shape."""
    options: list[FlightOption] = Field(
        description="Exactly 12 realistic flight options, sorted by total price ascending"
    )


class HoldResult(BaseModel):
    hold_id: str
    flight_id: str
    pax: int
    expires_at: str = Field(description="ISO-8601 UTC timestamp · hold expires 30 min after issue")
    status: str = Field(description="'held' on success")


# ─── Gemini structured-output generation (lifted from old flight_agent) ──

_redis = redis.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)
_CACHE_TTL_SECONDS = 24 * 60 * 60

_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_structured_llm = _llm.with_structured_output(_StructuredFlightOptions)


_SYSTEM = """You generate realistic flight inventory for an Indian travel concierge.
For ANY origin-destination route, produce 12 plausible flight options that match
the criteria, varying carriers, times, stops, and prices realistically.

Carrier mix:
  - Indian carriers: Air India, IndiGo, Vistara, Akasa, SpiceJet, Air India Express
  - International: ANA, Japan Airlines, Singapore Airlines, Emirates, Lufthansa,
    British Airways, Cathay Pacific, Thai Airways, Etihad

Pricing realism (INR per pax, one-way unless noted):
  - Domestic India short-haul: ₹3,500 - ₹9,000
  - Domestic India long: ₹6,000 - ₹14,000
  - India ↔ SE Asia (Bali, Bangkok, Singapore): ₹18,000 - ₹35,000
  - India ↔ Japan / Korea: ₹38,000 - ₹70,000
  - India ↔ Europe: ₹45,000 - ₹90,000
  - India ↔ Middle East: ₹15,000 - ₹30,000

Time realism:
  - Non-stop India ↔ Japan: ~9-10 hours
  - 1-stop with reasonable layover: add 3-6 hours
  - Domestic India: 1-3 hours

Stops: ~60% non-stop, ~30% 1-stop, ~10% 2-stop.
Departure times: spread across morning / afternoon / evening / night.
"""


def _example_for(origin: str, destination: str, depart: str, ret: str | None, pax: int) -> str:
    one_way = not ret
    multiplier = 1 if one_way else 2
    example = {
        "flight_id": "AI314",
        "airline": "Air India",
        "alliance": "Star Alliance",
        "origin": origin,
        "destination": destination,
        "depart_date": depart,
        "return_date": ret if not one_way else None,
        "dep_time": "10:30",
        "arr_time": "22:15 (+1 day)" if destination in ("NRT", "HND", "LHR", "JFK") else "13:45",
        "duration_hours": 10.5 if destination in ("NRT", "HND", "LHR", "JFK") else 3.0,
        "stops": 0,
        "price_per_pax_inr": 52000,
        "price_total_inr": 52000 * pax * multiplier,
        "pax": pax,
    }
    return json.dumps(example, indent=2)


def _user_prompt(origin: str, destination: str, depart: str, ret: str | None, pax: int) -> str:
    multiplier_note = "× 2 (round trip)" if ret else "× 1 (one-way)"
    return f"""Generate 12 flight options for:

  origin: {origin}
  destination: {destination}
  depart_date: {depart}
  return_date: {ret if ret else 'null (one-way)'}
  pax: {pax}

Vary carriers, times, stops, and prices realistically. Sort ascending by
price_total_inr.

Reference example for ONE option (use as a calibration, not a template · your
12 options should be DIFFERENT carriers / times / prices / stops):

{_example_for(origin, destination, depart, ret, pax)}

For each option:
  - price_total_inr = price_per_pax_inr × pax {multiplier_note}
"""


def _cache_key(origin: str, destination: str, depart: str, ret: str, pax: int) -> str:
    base = f"{origin}|{destination}|{depart}|{ret}|{pax}"
    h = hashlib.md5(base.encode()).hexdigest()[:12]
    return f"flight-options:{h}"


async def _generate_options(
    origin: str, destination: str, depart: str, ret: str | None, pax: int,
) -> list[dict]:
    response = await _structured_llm.ainvoke([
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_user_prompt(origin, destination, depart, ret, pax)),
    ])
    return [o.model_dump() for o in response.options]


# ─── MCP tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    pax: int = 2,
    return_date: Optional[str] = None,
) -> list[FlightOption]:
    """Return 12 realistic flight options for the given route, dates, and party size.

    Inputs:
      origin:       3-letter IATA code, e.g. 'BLR'
      destination:  3-letter IATA code, e.g. 'NRT'
      depart_date:  YYYY-MM-DD
      pax:          number of passengers (default 2)
      return_date:  YYYY-MM-DD for round-trip · omit / null for one-way

    Returns: list of FlightOption sorted ascending by price_total_inr.

    Results are cached in Redis for 24h keyed by (origin, destination, dates, pax).
    This tool does NOT pick a recommendation · that judgment belongs to the agent
    calling this tool.
    """
    origin = origin.upper()
    destination = destination.upper()
    ret_str = return_date or ""

    key = _cache_key(origin, destination, depart_date, ret_str, pax)
    cached_raw = await _redis.get(key)
    if cached_raw:
        log.info("mcp.airline.search.cache.hit key=%s", key)
        options_data = json.loads(cached_raw)
    else:
        log.info("mcp.airline.search.cache.miss key=%s · structured LLM call", key)
        options_data = await _generate_options(
            origin, destination, depart_date, return_date or None, pax,
        )
        await _redis.setex(key, _CACHE_TTL_SECONDS, json.dumps(options_data))

    return [FlightOption(**o) for o in options_data]


@mcp.tool()
async def hold_flight(flight_id: str, pax: int = 2) -> HoldResult:
    """Place a 30-minute hold on a specific flight.

    Inputs:
      flight_id:  the flight_id from a prior search_flights result
      pax:        number of passengers (must match the search)

    Returns: HoldResult with a hold_id and expiry timestamp.

    Mock implementation · always succeeds. In production this would call
    a real GDS / NDC endpoint and could raise on inventory loss.
    """
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return HoldResult(
        hold_id=f"HLD-{uuid.uuid4().hex[:8].upper()}",
        flight_id=flight_id,
        pax=pax,
        expires_at=expires.isoformat(),
        status="held",
    )


# ─── HTTP surface (health probe + MCP transport) ─────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "mcp-airline"})


# Streamable-HTTP ASGI app · uvicorn entry point.
# MCP JSON-RPC lives at /mcp (no trailing slash · /mcp/ → 307 redirect that
# curl does not replay POST bodies through).  Health probe at /health.
app = mcp.streamable_http_app()
