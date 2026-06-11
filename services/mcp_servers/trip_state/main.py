"""mcp-trip-state · MCP server for itinerary + todo state.

Phase 4 closer + Phase 5 starter. Centralises the trip's *content* state:
itineraries and todos. Same pattern as mcp-airline / mcp-hotel · Gemini
structured-output runs inside this server so the sub-agents (itinerary,
todo) only need to extract args from a natural-language brief and call
the tool.

Tools (MCP):
  - build_itinerary(city, days, interests?, hotel_neighborhood?)
        → ItineraryResponse  · {city, days[]}
  - revise_itinerary(itinerary_json, critique)
        → ItineraryResponse  · revised plan with note on each changed day
  - create_todos(destination, depart_date)
        → TodosResponse      · {count, todos[]}

Phase 5 will extend this server with budget tools (set_budget,
check_budget, commit_spend) once budget-agent is also migrated.

Transport: streamable-http (stateless).
Endpoint:  POST http://mcp-trip-state:9015/mcp
Health:    GET  http://mcp-trip-state:9015/health
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, timedelta
from typing import Optional

import redis.asyncio as redis
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from shared.observability import setup_observability

setup_observability("mcp-trip-state")

log = logging.getLogger(__name__)


# ─── MCP server ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "trip_state",
    instructions=(
        "Trip content tools · build/revise day-by-day itineraries and "
        "generate pre-trip todos. Used by itinerary-agent and todo-agent "
        "as their MCP-side data source."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-trip-state:9015",
            "mcp-trip-state",
            "localhost:9015",
            "localhost",
            "127.0.0.1:9015",
            "127.0.0.1",
        ],
        allowed_origins=["*"],
    ),
)


# ─── shared schemas ──────────────────────────────────────────────────────

class ItineraryItem(BaseModel):
    time: str = Field(description="HH:MM 24-hour clock")
    what: str = Field(description="What the traveller does · one concise line")


class ItineraryDay(BaseModel):
    day: int = Field(ge=1, description="Day number, 1-indexed")
    title: str = Field(description="Short title like 'Arrival · Shinjuku' or 'Food day · Tsukiji'")
    items: list[ItineraryItem] = Field(description="3-5 items spread across the day")
    notes: list[str] = Field(default_factory=list, description="Optional notes (esp. on revisions)")


class ItineraryResponse(BaseModel):
    city: str
    days: list[ItineraryDay]


class TodoItem(BaseModel):
    id: int
    text: str
    priority: str = Field(description="'high' | 'medium' | 'low'")
    due_date: str = Field(description="YYYY-MM-DD")


class TodosResponse(BaseModel):
    count: int
    todos: list[TodoItem]


class BudgetState(BaseModel):
    ok: bool
    limit_inr: int
    spent_inr: int
    remaining_inr: int
    categories: dict[str, int] = Field(default_factory=dict)


class CheckResult(BaseModel):
    ok: bool
    limit_inr: int
    spent_inr: int
    remaining_inr: int
    category: str
    proposed: int
    reason: Optional[str] = None


# ─── infra (Redis cache + LLM) ───────────────────────────────────────────

_redis = redis.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)
_CACHE_TTL_SECONDS = 24 * 60 * 60

_llm = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    temperature=0.4,
)
_itinerary_llm = _llm.with_structured_output(ItineraryResponse)


# ─── itinerary prompt ────────────────────────────────────────────────────

_ITINERARY_SYSTEM = """You build day-by-day travel itineraries for an Indian travel concierge.

Constraints:
- Day 1 is the ARRIVAL day · keep it light (2-3 items, mostly evening) so the
  traveller can recover from the flight.
- Final day is the DEPARTURE day · half-day at most, end with the transit to
  airport / station.
- Middle days: 3-5 items each. Group geographically (don't bounce across the
  city). Respect the traveller's interests (food/beach/culture/adventure).
- Use real local landmarks, neighborhoods, restaurants, markets, temples,
  experiences. Name actual places.
- Times in 24-hour clock. Don't pack too tightly · leave room to breathe.
- If hotel_neighborhood is given, start mornings near there when possible.
"""


def _itinerary_user_prompt(city: str, days: int, interests: list[str], neighborhood: str) -> str:
    interests_str = ", ".join(interests) if interests else "general sightseeing"
    nbhd_str = f"Hotel neighborhood: {neighborhood}" if neighborhood else "No specific home base"
    return f"""Build a {days}-day itinerary for {city}.

  Interests: {interests_str}
  {nbhd_str}

Day 1 is arrival, day {days} is departure. Middle days are the meat of the trip.
3-5 items per day. Real places. Tight grouping per day.
"""


def _itinerary_cache_key(city: str, days: int, interests: list[str], neighborhood: str) -> str:
    base = f"{city.lower()}|{days}|{','.join(sorted(interests))}|{neighborhood.lower()}"
    h = hashlib.md5(base.encode()).hexdigest()[:12]
    return f"itinerary:{h}"


# ─── itinerary tools ─────────────────────────────────────────────────────

@mcp.tool()
async def build_itinerary(
    city: str,
    days: int = 4,
    interests: Optional[list[str]] = None,
    hotel_neighborhood: Optional[str] = None,
) -> ItineraryResponse:
    """Build a day-by-day itinerary for `city` over `days` days.

    Inputs:
      city:               city name (e.g. 'Tokyo', 'Paris')
      days:               trip length, including arrival and departure days
      interests:          list of strings like ['food', 'culture', 'shopping']
      hotel_neighborhood: optional · helps anchor mornings near the hotel

    Returns: ItineraryResponse with `days` list (each day has items + notes).

    Day 1 stays light (jet lag), final day is half-day, middle days are 3-5
    items each. Cached in Redis 24h keyed by (city, days, interests, nbhd).
    """
    city = city.strip()
    interests = list(interests or [])
    hotel_neighborhood = (hotel_neighborhood or "").strip()

    key = _itinerary_cache_key(city, days, interests, hotel_neighborhood)
    cached = await _redis.get(key)
    if cached:
        log.info("mcp.trip_state.itinerary.cache.hit key=%s", key)
        return ItineraryResponse(**json.loads(cached))

    log.info("mcp.trip_state.itinerary.cache.miss key=%s · structured LLM call", key)
    response = await _itinerary_llm.ainvoke([
        SystemMessage(content=_ITINERARY_SYSTEM),
        HumanMessage(content=_itinerary_user_prompt(city, days, interests, hotel_neighborhood)),
    ])
    await _redis.setex(key, _CACHE_TTL_SECONDS, response.model_dump_json())
    return response


@mcp.tool()
async def revise_itinerary(
    itinerary_json: str,
    critique: str,
) -> ItineraryResponse:
    """Apply a critique to an existing itinerary and return the revised version.

    Inputs:
      itinerary_json: the current itinerary, JSON-encoded as a string
                      (shape: {"city": str, "days": [{day, title, items, notes}]})
      critique:       what to change (typically from critic-agent's review)

    Returns: ItineraryResponse · same shape, with `notes` on each modified
    day explaining what changed.

    Only changes what the critique flags; preserves what works.
    """
    try:
        current = json.loads(itinerary_json)
    except (ValueError, TypeError):
        current = {"city": "", "days": []}

    current_days = current.get("days", []) if isinstance(current, dict) else []
    n_days = len(current_days)
    prompt = f"""Revise this itinerary based on the critique. Keep what works.
Only change what the critique flags.

CRITIQUE:
{critique}

CURRENT ITINERARY ({n_days} days):
{json.dumps(current, indent=2)}

## CRITICAL · RETURN ALL {n_days} DAYS

You MUST return ALL {n_days} days in the `days` array, not just the ones
you modified. The output `days` array must have EXACTLY {n_days} entries.

For days you DID NOT modify: copy them VERBATIM from the input (same
day number, same title, same items, same notes).

For days you DID modify (per the critique): emit the revised version
AND add a `notes` entry on that day explaining what changed.

If you return fewer than {n_days} days, the user's UI will drop the
missing days and they'll lose the rest of their plan. This is a HARD
requirement · do not truncate.
"""
    response = await _itinerary_llm.ainvoke([
        SystemMessage(content=_ITINERARY_SYSTEM),
        HumanMessage(content=prompt),
    ])

    # Safeguard · Gemini sometimes returns ONLY the modified day(s) instead of
    # the full plan, which would silently truncate the user's itinerary on
    # the next upsert. If the LLM returned fewer days than the input had,
    # merge the revised days back into a copy of the input (matched by day
    # number) so the full N-day plan is preserved.
    if n_days and len(response.days) < n_days:
        log.warning(
            "mcp.trip_state.revise · LLM returned %d days but input had %d · "
            "merging revised days back into input",
            len(response.days), n_days,
        )
        revised_by_day = {d.day: d for d in response.days}
        merged_days: list[ItineraryDay] = []
        for cd in current_days:
            day_num = cd.get("day") if isinstance(cd, dict) else None
            if day_num is not None and day_num in revised_by_day:
                merged_days.append(revised_by_day[day_num])
            else:
                # Preserve untouched day verbatim · parse from input dict.
                try:
                    merged_days.append(ItineraryDay(**cd))
                except Exception:
                    pass
        response = ItineraryResponse(city=response.city or current.get("city", ""), days=merged_days)

    return response


# ─── todos ───────────────────────────────────────────────────────────────

_DEFAULT_TODO_TEMPLATES: list[dict] = [
    {"text": "Renew passport if expiring within 6 months", "priority": "high",   "offset_days": -45},
    {"text": "Apply for visa / e-Visa",                    "priority": "high",   "offset_days": -30},
    {"text": "Buy travel insurance",                       "priority": "medium", "offset_days": -14},
    {"text": "Notify bank of international travel",        "priority": "medium", "offset_days": -7},
    {"text": "Exchange / load forex card",                 "priority": "medium", "offset_days": -5},
    {"text": "Pack camera + adapters + power bank",        "priority": "low",    "offset_days": -2},
    {"text": "Online check-in 24h before flight",          "priority": "medium", "offset_days": -1},
]


@mcp.tool()
async def create_todos(
    destination: str,
    depart_date: str,
) -> TodosResponse:
    """Generate a standard pre-trip todo list with due dates anchored to departure.

    Inputs:
      destination:  where the trip is to (informational; future versions
                    will adapt todos per destination · e.g. scooter license
                    for Bali, dive cert for Maldives, etc.)
      depart_date:  YYYY-MM-DD · used as the anchor for relative due dates

    Returns: TodosResponse with 7 standard todos covering passport / visa /
    insurance / bank notification / forex / packing / check-in.

    Mock implementation · returns the same template list with dates
    computed from depart_date. Phase 5 polish will LLM-adapt the list
    based on destination specifics.
    """
    try:
        depart = date.fromisoformat(depart_date)
    except ValueError:
        depart = date.today() + timedelta(days=60)

    todos = [
        TodoItem(
            id=i + 1,
            text=t["text"],
            priority=t["priority"],
            due_date=(depart + timedelta(days=t["offset_days"])).isoformat(),
        )
        for i, t in enumerate(_DEFAULT_TODO_TEMPLATES)
    ]
    return TodosResponse(count=len(todos), todos=todos)


# ─── budget tools (Phase 5 · replaces budget-agent) ──────────────────────

def _budget_key(trip_id: str) -> str:
    return f"budget:{trip_id}"


async def _load_budget(trip_id: str) -> dict:
    raw = await _redis.get(_budget_key(trip_id))
    if raw:
        return json.loads(raw)
    return {"limit_inr": 200_000, "spent_inr": 0, "categories": {}}


async def _save_budget(trip_id: str, data: dict) -> None:
    await _redis.set(_budget_key(trip_id), json.dumps(data))


@mcp.tool()
async def set_budget(trip_id: str, limit_inr: int) -> BudgetState:
    """Set the total trip budget ceiling (resets spent + categories to zero).

    Call once at the start of trip planning once the user's budget is known.
    """
    data = {"limit_inr": int(limit_inr), "spent_inr": 0, "categories": {}}
    await _save_budget(trip_id, data)
    return BudgetState(
        ok=True, limit_inr=data["limit_inr"], spent_inr=0,
        remaining_inr=data["limit_inr"], categories={},
    )


@mcp.tool()
async def check_budget(
    trip_id: str,
    proposed_amount_inr: int,
    category: str = "uncategorized",
) -> CheckResult:
    """Check whether a proposed spend fits the remaining budget.

    Returns ok=true if it fits. Use BEFORE commit_spend.
    """
    data = await _load_budget(trip_id)
    remaining = data["limit_inr"] - data["spent_inr"]
    proposed = int(proposed_amount_inr)
    ok = proposed <= remaining
    return CheckResult(
        ok=ok,
        limit_inr=data["limit_inr"],
        spent_inr=data["spent_inr"],
        remaining_inr=remaining,
        category=category,
        proposed=proposed,
        reason=None if ok else f"₹{proposed} exceeds remaining ₹{remaining}",
    )


@mcp.tool()
async def commit_spend(
    trip_id: str,
    amount_inr: int,
    category: str = "uncategorized",
    description: str = "",
) -> BudgetState:
    """Commit a spend against the trip budget (call AFTER check_budget returns ok).

    Persists the spend by category so the UI's budget bar reflects totals.
    """
    data = await _load_budget(trip_id)
    amt = int(amount_inr)
    data["spent_inr"] += amt
    data["categories"][category] = data["categories"].get(category, 0) + amt
    await _save_budget(trip_id, data)
    log.info(
        "mcp.trip_state.budget.commit trip=%s amount=%d cat=%s desc=%s spent=%d",
        trip_id, amt, category, description, data["spent_inr"],
    )
    return BudgetState(
        ok=True,
        limit_inr=data["limit_inr"],
        spent_inr=data["spent_inr"],
        remaining_inr=data["limit_inr"] - data["spent_inr"],
        categories=data["categories"],
    )


@mcp.tool()
async def get_budget(trip_id: str) -> BudgetState:
    """Get the current budget state for a trip."""
    data = await _load_budget(trip_id)
    return BudgetState(
        ok=True,
        limit_inr=data["limit_inr"],
        spent_inr=data["spent_inr"],
        remaining_inr=data["limit_inr"] - data["spent_inr"],
        categories=data["categories"],
    )


# ─── HTTP surface (health + MCP transport) ───────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "mcp-trip-state"})


app = mcp.streamable_http_app()
