"""hotel-agent · LLM-generated hotel options with structured output.

Same pattern as flight_agent: with_structured_output enforces the schema
via Gemini's native function calling. Cached in Redis by (city, dates, pax).

A2A skills:
  - search_hotels(city, check_in, check_out, pax, max_per_night_inr?) → {options, recommended_id, recommended}
  - hold_hotel(hotel_id, check_in, check_out) → {hold_id, ...}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import redis.asyncio as redis
from fastapi import FastAPI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("hotel-agent")

app = FastAPI(title="hotel-agent", version="0.3.0")
a2a = A2AServer(
    app,
    agent_name="hotel-agent",
    description="Searches and holds hotels. Structured LLM output, Redis-cached.",
)

log = logging.getLogger(__name__)

_r = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
_CACHE_TTL_SECONDS = 24 * 60 * 60


# ─── structured output schema ────────────────────────────────────────────

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


class HotelOptionsResponse(BaseModel):
    options: list[HotelOption] = Field(description="Exactly 12 realistic hotel options, sorted by rating descending")


_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_structured_llm = _llm.with_structured_output(HotelOptionsResponse)


# ─── prompts ─────────────────────────────────────────────────────────────

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


def _nights(check_in: str, check_out: str) -> int:
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        return max(1, (co - ci).days)
    except ValueError:
        return 1


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


# ─── HTTP ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "hotel-agent"}


@a2a.skill("search_hotels")
async def search_hotels(payload: dict) -> dict:
    city = (payload.get("city") or "tokyo").strip()
    check_in = payload.get("check_in") or "2026-10-15"
    check_out = payload.get("check_out") or "2026-10-19"
    pax = int(payload.get("pax", 2))
    max_per_night_inr = payload.get("max_per_night_inr")

    key = _cache_key(city, check_in, check_out, pax)

    cached_raw = await _r.get(key)
    if cached_raw:
        log.info("hotel.cache.hit key=%s", key)
        options = json.loads(cached_raw)
    else:
        log.info("hotel.cache.miss key=%s · structured LLM call", key)
        try:
            options = await _generate_options(city, check_in, check_out, pax)
        except Exception as e:
            log.exception("hotel.generate.failed")
            return {
                "options": [],
                "recommended_id": None,
                "recommended": None,
                "error": f"could not generate hotel options: {e}",
            }
        await _r.setex(key, _CACHE_TTL_SECONDS, json.dumps(options))

    candidates = options
    if max_per_night_inr:
        in_budget = [o for o in options if o.get("per_night_inr", 0) <= int(max_per_night_inr)]
        candidates = in_budget if in_budget else options
    if candidates:
        recommended = max(candidates, key=lambda h: (h.get("rating", 0), -h.get("per_night_inr", 0)))
    else:
        recommended = None

    return {
        "options": options,
        "recommended_id": recommended["hotel_id"] if recommended else None,
        "recommended": recommended,
    }


@a2a.skill("hold_hotel")
async def hold_hotel(payload: dict) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return {
        "hold_id": f"HLD-{uuid.uuid4().hex[:8].upper()}",
        "hotel_id": payload["hotel_id"],
        "check_in": payload["check_in"],
        "check_out": payload["check_out"],
        "expires_at": expires.isoformat(),
        "status": "held",
    }
