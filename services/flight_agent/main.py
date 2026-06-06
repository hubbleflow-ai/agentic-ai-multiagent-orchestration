"""flight-agent · LLM-generated flight options with structured output.

Switched from regex JSON extraction to LangChain's `with_structured_output`,
which uses Gemini's native function-calling to ENFORCE the Pydantic schema.
No more parse errors — if Gemini can't produce a valid response, it raises
a clean ValidationError we catch and return as an A2A error.

Caches by (origin, destination, depart, return, pax) in Redis with 24h TTL.

A2A skills:
  - search_flights(origin, destination, depart_date, return_date, pax, max_budget_inr)
       → {options, recommended_id, recommended}
  - hold_flight(flight_id, pax) → {hold_id, expires_at}
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
from fastapi import FastAPI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("flight-agent")

app = FastAPI(title="flight-agent", version="0.3.0")
a2a = A2AServer(
    app,
    agent_name="flight-agent",
    description="Searches and holds flights. Structured LLM output, Redis-cached.",
)

log = logging.getLogger(__name__)

_r = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
_CACHE_TTL_SECONDS = 24 * 60 * 60


# ─── structured output schema (enforced by Gemini function-calling) ──────

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
    price_total_inr: int = Field(description="Total price = per_pax * pax * (2 if round-trip, 1 if one-way)")
    pax: int = Field(description="Number of passengers in this booking")


class FlightOptionsResponse(BaseModel):
    options: list[FlightOption] = Field(description="Exactly 12 realistic flight options, sorted by total price ascending")


_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_structured_llm = _llm.with_structured_output(FlightOptionsResponse)


# ─── prompts ─────────────────────────────────────────────────────────────

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
    # A single concrete example calibrates the model's price + time sense
    # without constraining its option diversity.
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

Reference example for ONE option (use as a calibration, not a template — your
12 options should be DIFFERENT carriers / times / prices / stops):

{_example_for(origin, destination, depart, ret, pax)}

For each option:
  - price_total_inr = price_per_pax_inr × pax {multiplier_note}
"""


# ─── caching ─────────────────────────────────────────────────────────────

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
    # response is a validated FlightOptionsResponse instance.
    return [o.model_dump() for o in response.options]


# ─── HTTP ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "flight-agent"}


@a2a.skill("search_flights")
async def search_flights(payload: dict) -> dict:
    origin = (payload.get("origin") or "BLR").upper()
    destination = (payload.get("destination") or "NRT").upper()
    depart_date = payload.get("depart_date") or "2026-10-15"
    return_date = payload.get("return_date") or ""
    pax = int(payload.get("pax", 2))
    max_budget_inr = payload.get("max_budget_inr")

    key = _cache_key(origin, destination, depart_date, return_date, pax)

    cached_raw = await _r.get(key)
    if cached_raw:
        log.info("flight.cache.hit key=%s", key)
        options = json.loads(cached_raw)
    else:
        log.info("flight.cache.miss key=%s · structured LLM call", key)
        try:
            options = await _generate_options(
                origin, destination, depart_date, return_date or None, pax,
            )
        except Exception as e:
            log.exception("flight.generate.failed")
            return {
                "options": [],
                "recommended_id": None,
                "recommended": None,
                "error": f"could not generate flight options: {e}",
            }
        await _r.setex(key, _CACHE_TTL_SECONDS, json.dumps(options))

    # Recommend: cheapest under budget, else cheapest overall.
    candidates = options
    if max_budget_inr:
        in_budget = [o for o in options if o.get("price_total_inr", 0) <= int(max_budget_inr)]
        candidates = in_budget if in_budget else options
    recommended = min(candidates, key=lambda o: o.get("price_total_inr", 0)) if candidates else None

    return {
        "options": options,
        "recommended_id": recommended["flight_id"] if recommended else None,
        "recommended": recommended,
    }


@a2a.skill("hold_flight")
async def hold_flight(payload: dict) -> dict:
    flight_id = payload["flight_id"]
    pax = int(payload.get("pax", 2))
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return {
        "hold_id": f"HLD-{uuid.uuid4().hex[:8].upper()}",
        "flight_id": flight_id,
        "pax": pax,
        "expires_at": expires.isoformat(),
        "status": "held",
    }
