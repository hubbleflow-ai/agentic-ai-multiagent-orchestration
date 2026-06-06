"""itinerary-agent · LLM-generated day-by-day itineraries.

Same pattern as flight/hotel agents: Gemini with structured output via
Pydantic schema. Cached in Redis by (city, days, interests, neighborhood).

A2A skills:
  - build_itinerary(city, days, interests, hotel_neighborhood?) → {city, days[]}
  - revise_itinerary(itinerary_json | itinerary, critique) → {city, days[]}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

import redis.asyncio as redis
from fastapi import FastAPI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("itinerary-agent")

app = FastAPI(title="itinerary-agent", version="0.3.0")
a2a = A2AServer(
    app,
    agent_name="itinerary-agent",
    description="Generates and revises day-by-day itineraries via LLM.",
)

log = logging.getLogger(__name__)

_r = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
_CACHE_TTL_SECONDS = 24 * 60 * 60


class ItineraryItem(BaseModel):
    time: str = Field(description="HH:MM 24-hour clock")
    what: str = Field(description="What the traveller does · one concise line")


class ItineraryDay(BaseModel):
    day: int = Field(ge=1, description="Day number, 1-indexed")
    title: str = Field(description="Short title like 'Arrival · Shinjuku' or 'Food day · Tsukiji'")
    items: list[ItineraryItem] = Field(description="3-5 items spread across the day")
    notes: list[str] = Field(default_factory=list, description="Optional notes (only when revisions add context)")


class ItineraryResponse(BaseModel):
    city: str
    days: list[ItineraryDay]


_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_structured_llm = _llm.with_structured_output(ItineraryResponse)


_SYSTEM = """You build day-by-day travel itineraries for an Indian travel concierge.

Constraints:
- Day 1 is the ARRIVAL day — keep it light (2-3 items, mostly evening) so the
  traveller can recover from the flight.
- Final day is the DEPARTURE day — half-day at most, end with the transit to
  airport / station.
- Middle days: 3-5 items each. Group geographically (don't bounce across the
  city). Respect the traveller's interests (food/beach/culture/adventure).
- Use real local landmarks, neighborhoods, restaurants, markets, temples,
  experiences. Name actual places.
- Times in 24-hour clock. Don't pack too tightly — leave room to breathe.
- If hotel_neighborhood is given, start mornings near there when possible.
"""


def _cache_key(city: str, days: int, interests: list[str], neighborhood: str) -> str:
    base = f"{city.lower()}|{days}|{','.join(sorted(interests))}|{neighborhood.lower()}"
    h = hashlib.md5(base.encode()).hexdigest()[:12]
    return f"itinerary:{h}"


def _user_prompt(city: str, days: int, interests: list[str], neighborhood: str) -> str:
    interests_str = ", ".join(interests) if interests else "general sightseeing"
    nbhd_str = f"Hotel neighborhood: {neighborhood}" if neighborhood else "No specific home base"
    return f"""Build a {days}-day itinerary for {city}.

  Interests: {interests_str}
  {nbhd_str}

Day 1 is arrival, day {days} is departure. Middle days are the meat of the trip.
3-5 items per day. Real places. Tight grouping per day.
"""


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "itinerary-agent"}


@a2a.skill("build_itinerary")
async def build_itinerary(payload: dict) -> dict:
    city = (payload.get("city") or "tokyo").strip()
    days = int(payload.get("days", 4))
    interests = list(payload.get("interests") or [])
    hotel_neighborhood = (payload.get("hotel_neighborhood") or "").strip()

    key = _cache_key(city, days, interests, hotel_neighborhood)
    cached = await _r.get(key)
    if cached:
        log.info("itinerary.cache.hit key=%s", key)
        return json.loads(cached)

    log.info("itinerary.cache.miss key=%s · structured LLM call", key)
    try:
        response = await _structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=_user_prompt(city, days, interests, hotel_neighborhood)),
        ])
    except Exception as e:
        log.exception("itinerary.generate.failed")
        return {"city": city.title(), "days": [], "error": str(e)}

    result = {
        "city": response.city,
        "days": [d.model_dump() for d in response.days],
    }
    await _r.setex(key, _CACHE_TTL_SECONDS, json.dumps(result))
    return result


@a2a.skill("revise_itinerary")
async def revise_itinerary(payload: dict) -> dict:
    """Apply a critique to an existing itinerary."""
    itinerary = payload.get("itinerary")
    if isinstance(itinerary, str):
        try:
            itinerary = json.loads(itinerary)
        except json.JSONDecodeError:
            itinerary = {"city": "", "days": []}
    itinerary = itinerary or {}
    critique = payload.get("critique") or ""

    prompt = f"""Revise this itinerary based on the critique. Keep what works.
Only change what the critique flags.

CRITIQUE:
{critique}

CURRENT ITINERARY:
{json.dumps(itinerary, indent=2)}

Return the full revised itinerary (same shape). Add a brief note to each
modified day's `notes` array explaining what changed.
"""
    try:
        response = await _structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=prompt),
        ])
    except Exception as e:
        log.exception("itinerary.revise.failed")
        return {"city": itinerary.get("city", ""), "days": itinerary.get("days", []), "error": str(e)}

    return {
        "city": response.city,
        "days": [d.model_dump() for d in response.days],
        "revision_applied": True,
    }
