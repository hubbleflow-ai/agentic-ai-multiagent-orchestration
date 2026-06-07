"""mcp-hotel · MCP server exposing hotel inventory tools.

Non-reasoning service · Gemini structured-output generates 12 realistic
hotel options per (city, dates, pax), cached in Redis. Mirror of mcp-airline.

Tools (MCP):
  - search_hotels(city, check_in, check_out, pax, max_per_night_inr?)
        → list[HotelOption]  (12 options, sorted by rating descending)
  - hold_hotel(hotel_id, check_in, check_out)
        → HoldResult  (30-minute hold)

Transport: streamable-http (stateless).
Endpoint:  POST http://mcp-hotel:9012/mcp     (JSON-RPC 2.0)
Health:    GET  http://mcp-hotel:9012/health

Lifted verbatim from the previous services/hotel_agent/main.py · per the
Session 6 refactor plan, the agent layer (Phase 4) reasons over these
options; the MCP layer provides the raw inventory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from shared.observability import setup_observability

setup_observability("mcp-hotel")

log = logging.getLogger(__name__)


# ─── MCP server ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "hotel",
    instructions=(
        "Realistic hotel-inventory tools for an Indian travel concierge. "
        "Call search_hotels to get 12 options for a city + dates; "
        "call hold_hotel to place a 30-minute hold on one of them."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-hotel:9012",
            "mcp-hotel",
            "localhost:9012",
            "localhost",
            "127.0.0.1:9012",
            "127.0.0.1",
        ],
        allowed_origins=["*"],
    ),
)


# ─── tool schemas ────────────────────────────────────────────────────────

class HotelOption(BaseModel):
    hotel_id: str = Field(description="Short id like 'HTL-TOK-001'")
    name: str = Field(description="Realistic hotel name")
    city: str
    neighborhood: str = Field(description="Specific neighborhood / area name")
    rating: float = Field(ge=3.0, le=5.0, description="Star rating 3.0-5.0")
    amenities: list[str] = Field(description="2-4 short tags like 'pool', 'spa', 'beach', 'rooftop'")
    per_night_inr: int
    nights: int
    total_inr: int = Field(description="per_night_inr × nights")
    pax: int
    # Filled in by search_hotels from the call args · the Gemini structured
    # output doesn't generate dates (they're inputs, not outputs), so we
    # stamp them on every returned option for the consumer's stay key.
    check_in: str = Field(description="YYYY-MM-DD (set from search args)")
    check_out: str = Field(description="YYYY-MM-DD (set from search args)")


class _StructuredHotelOptions(BaseModel):
    """Internal-only · constrains Gemini's response shape."""
    options: list[HotelOption] = Field(
        description="Exactly 12 realistic hotel options, sorted by rating descending"
    )


class HoldResult(BaseModel):
    hold_id: str
    hotel_id: str
    check_in: str
    check_out: str
    expires_at: str = Field(description="ISO-8601 UTC timestamp · hold expires 30 min after issue")
    status: str = Field(description="'held' on success")


# ─── Gemini structured-output generation ─────────────────────────────────

_redis = redis.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)
_CACHE_TTL_SECONDS = 24 * 60 * 60

_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_structured_llm = _llm.with_structured_output(_StructuredHotelOptions)


_SYSTEM = """You generate realistic hotel inventory for an Indian travel concierge.
For ANY city + date range, produce 12 plausible hotel options that mix
budget / mid-range / luxury across the city's distinct neighborhoods.

Pricing realism (per night, INR):
  - Tokyo: ₹6k budget · ₹10-15k mid · ₹18-25k luxury
  - Goa:   ₹4-7k budget · ₹8-14k mid · ₹15-30k luxury
  - Bali:  ₹4-8k budget · ₹9-18k mid · ₹20-40k luxury
  - European cities: ₹8-12k budget · ₹15-25k mid · ₹30-50k luxury
  - Indian metros (Delhi/Mumbai/Bangalore): ₹4-7k · ₹8-15k · ₹18-35k
  - Indian tier-2 (Jaipur/Udaipur/Kochi): ₹3-6k · ₹7-12k · ₹15-25k

Use real-sounding hotel names: international chains (Hyatt, Marriott, Taj,
ITC, Oberoi, Hilton, Four Seasons, Ritz-Carlton, Park Hyatt, W, Aman) AND
boutique/local picks. Spread across the city's actual neighborhoods.

Amenities are short tags: pool, spa, beach, rooftop, gym, breakfast,
view, central, design, onsen, ryokan, heritage, nightlife.

Ratings: realistic for the price tier (budget ~3.5-4.0, mid ~4.0-4.5, luxury ~4.5-5.0).
"""


def _nights(check_in: str, check_out: str) -> int:
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        return max(1, (co - ci).days)
    except ValueError:
        return 1


def _user_prompt(city: str, check_in: str, check_out: str, pax: int, nights: int) -> str:
    example = {
        "hotel_id": "HTL-EXM-001",
        "name": "Park Hyatt Example",
        "city": city.title(),
        "neighborhood": "Central",
        "rating": 4.7,
        "amenities": ["spa", "view", "pool"],
        "per_night_inr": 18000,
        "nights": nights,
        "total_inr": 18000 * nights,
        "pax": pax,
    }
    return f"""Generate 12 hotel options for:

  city: {city}
  check_in: {check_in}
  check_out: {check_out}
  pax: {pax}
  nights: {nights}

Mix budget / mid / luxury across the city's distinct neighborhoods. Use
realistic names. Sort by rating descending.

Reference example for ONE option (do not copy; use as calibration):

{json.dumps(example, indent=2)}

For each option:
  - total_inr = per_night_inr × {nights}
"""


def _cache_key(city: str, check_in: str, check_out: str, pax: int) -> str:
    base = f"{city.lower()}|{check_in}|{check_out}|{pax}"
    h = hashlib.md5(base.encode()).hexdigest()[:12]
    return f"hotel-options:{h}"


async def _generate_options(city: str, check_in: str, check_out: str, pax: int) -> list[dict]:
    nights = _nights(check_in, check_out)
    response = await _structured_llm.ainvoke([
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=_user_prompt(city, check_in, check_out, pax, nights)),
    ])
    return [o.model_dump() for o in response.options]


# ─── MCP tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def search_hotels(
    city: str,
    check_in: str,
    check_out: str,
    pax: int = 2,
    max_per_night_inr: Optional[int] = None,
) -> list[HotelOption]:
    """Return 12 realistic hotel options for the given city + dates + party size.

    Inputs:
      city:               e.g. 'Tokyo', 'Paris', 'Goa'
      check_in:           YYYY-MM-DD
      check_out:          YYYY-MM-DD
      pax:                number of guests (default 2)
      max_per_night_inr:  optional cap · filters but doesn't fail if empty

    Returns: list of HotelOption sorted descending by rating.

    Results cached in Redis 24h keyed by (city, dates, pax). This tool does
    NOT pick a recommendation · that judgment belongs to the agent calling it.
    """
    city = city.strip()
    key = _cache_key(city, check_in, check_out, pax)
    cached_raw = await _redis.get(key)
    if cached_raw:
        log.info("mcp.hotel.search.cache.hit key=%s", key)
        options_data = json.loads(cached_raw)
    else:
        log.info("mcp.hotel.search.cache.miss key=%s · structured LLM call", key)
        options_data = await _generate_options(city, check_in, check_out, pax)
        await _redis.setex(key, _CACHE_TTL_SECONDS, json.dumps(options_data))

    if max_per_night_inr is not None:
        cap = int(max_per_night_inr)
        filtered = [o for o in options_data if o.get("per_night_inr", 0) <= cap]
        if filtered:
            options_data = filtered

    # Stamp the call's dates onto every option so consumers can key by stay.
    for o in options_data:
        o.setdefault("check_in", check_in)
        o.setdefault("check_out", check_out)

    return [HotelOption(**o) for o in options_data]


@mcp.tool()
async def hold_hotel(hotel_id: str, check_in: str, check_out: str) -> HoldResult:
    """Place a 30-minute hold on a specific hotel for the given check-in/out.

    Inputs:
      hotel_id:   the hotel_id from a prior search_hotels result
      check_in:   YYYY-MM-DD
      check_out:  YYYY-MM-DD

    Returns: HoldResult with a hold_id and expiry timestamp.

    Mock implementation · always succeeds. In production this would call a
    real PMS / channel manager and could raise on inventory loss.
    """
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return HoldResult(
        hold_id=f"HLD-{uuid.uuid4().hex[:8].upper()}",
        hotel_id=hotel_id,
        check_in=check_in,
        check_out=check_out,
        expires_at=expires.isoformat(),
        status="held",
    )


# ─── HTTP surface (health + MCP transport) ───────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "mcp-hotel"})


# Streamable-HTTP ASGI app · uvicorn entry point.
# MCP JSON-RPC at /mcp (no trailing slash).  Health probe at /health.
app = mcp.streamable_http_app()
