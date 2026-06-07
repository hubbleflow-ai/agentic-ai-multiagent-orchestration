"""planner · LangChain @tool wrappers + bound TOOLS list.

Three families of tools:

  1. Delegation tools (delegate_to_*_agent) · these wrap A2A calls to
     real sub-agents using shared/a2a_helpers/client.py. The sub-agent
     returns {text, artifacts}; the planner LLM gets that as a JSON
     string and the frontend extracts the structured artifacts.

  2. MCP-backed tools (set_budget / check_budget / commit_spend /
     authorize_payment / capture_payment / add_calendar_events) · these
     wrap direct calls to the 3 MCP servers (trip-state, payment,
     calendar). The wrapper @tool's name is what the LLM sees; under
     the hood it calls `call_mcp("<mcp_tool_name>", args)`.

  3. Helpers: `_trip_id` ContextVar (carries the session id so the
     MCP-backed tools can pass it to trip-state), and `_wrap` (JSON-
     encodes dict / list results because @tool must return a string).
"""

from __future__ import annotations

import json
from contextvars import ContextVar

from langchain_core.tools import tool

from services.planner.config import (
    CRITIC_AGENT_A2A_URL,
    FLIGHT_AGENT_A2A_URL,
    HOTEL_AGENT_A2A_URL,
    ITINERARY_AGENT_A2A_URL,
    RESEARCH_AGENT_A2A_URL,
    TODO_AGENT_A2A_URL,
)
from services.planner.mcp_client import call_mcp
from shared.a2a_helpers import delegate_to_a2a_agent


# ─── per-request trip_id ContextVar ──────────────────────────────────────
# Each agent run binds the session id to this ContextVar inside the SSE
# generator. The MCP-backed tools below read it so the LLM doesn't have
# to thread trip_id through every call.

_trip_id: ContextVar[str] = ContextVar("trip_id", default="unbound")


def _wrap(result: dict | list | str) -> str:
    """LangChain @tool must return str. JSON-encode dict/list results so
    the LLM can reason about structured fields naturally."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


# ─── delegation tools (A2A to reasoning sub-agents) ──────────────────────

@tool
async def delegate_to_research_agent(brief: str) -> str:
    """Delegate destination research to the research specialist (A2A).

    The research-agent is a text-only LLM sub-agent. Pass it a brief like
    "Tell me about Tokyo for an Indian traveller in October" and it returns
    one paragraph covering visa, weather, neighborhoods, must-see, gotchas.

    Returns a JSON object {text, artifacts} · text is the briefing; the
    artifacts list will be empty (research-agent doesn't emit cards).
    Use the text to anchor your subsequent flight/hotel recommendations
    but DO NOT regurgitate the whole paragraph back to the user verbatim ·
    extract the 1-2 most relevant points for the current decision.
    """
    result = await delegate_to_a2a_agent(RESEARCH_AGENT_A2A_URL, brief)
    return _wrap(result)


@tool
async def delegate_to_flight_agent(brief: str) -> str:
    """Delegate a flight-related task to the flight specialist sub-agent (A2A).

    Returns a JSON object (encoded as string) with two top-level fields:

      "text"      · the sub-agent's natural-language reply (recommendation
                    + backups, or hold confirmation). PARAPHRASE this for
                    the user; do NOT enumerate option lists or prices.

      "artifacts" · structured outputs · UI renders flight cards from these.

    Pattern of use:
        1. First call: search-and-recommend brief
        2. Surface the recommendation, wait for user pick
        3. Second call: hold brief
        4. Then check_budget + commit_spend
    """
    result = await delegate_to_a2a_agent(FLIGHT_AGENT_A2A_URL, brief)
    return _wrap(result)


@tool
async def delegate_to_hotel_agent(brief: str) -> str:
    """Delegate a hotel-related task to the hotel specialist sub-agent (A2A).

    Same pattern as delegate_to_flight_agent. ONE city stay per call ·
    NEVER bundle multiple stays. UI renders hotel cards from the
    search_hotels artifact.
    """
    result = await delegate_to_a2a_agent(HOTEL_AGENT_A2A_URL, brief)
    return _wrap(result)


@tool
async def delegate_to_itinerary_agent(brief: str) -> str:
    """Delegate itinerary build/revise to the itinerary specialist (A2A).

    For BUILD pass: "Build a 4-day Tokyo itinerary, foodie interests".
    For REVISE pass: "Revise this itinerary: {<JSON>} CRITIQUE: <text>".

    Returns {text, artifacts}; UI renders day cards from the artifact.
    Reply in ONE sentence · don't enumerate days in your text.
    """
    result = await delegate_to_a2a_agent(ITINERARY_AGENT_A2A_URL, brief)
    return _wrap(result)


@tool
async def delegate_to_critic_agent(brief: str) -> str:
    """Delegate artifact critique to the critic specialist (A2A).

    Pass a brief embedding the artifact:
        "Review this 4-day Tokyo itinerary: {...}. Lands at 06:00 Day 1."

    Returns {text, artifacts} · text is the critique paragraph; artifacts
    empty. If issues are flagged, re-call delegate_to_itinerary_agent in
    REVISE mode with the critique to apply fixes.
    """
    result = await delegate_to_a2a_agent(CRITIC_AGENT_A2A_URL, brief)
    return _wrap(result)


@tool
async def delegate_to_todo_agent(brief: str) -> str:
    """Delegate pre-trip todo generation to the todo specialist (A2A).

    Brief shape: "Create pre-trip todos for Tokyo with departure on 2026-10-15".

    Returns {text, artifacts} · UI renders the todos panel from the
    create_todos artifact. Don't enumerate todos in your text.
    """
    result = await delegate_to_a2a_agent(TODO_AGENT_A2A_URL, brief)
    return _wrap(result)


# ─── budget tools (direct MCP to trip-state) ─────────────────────────────

@tool
async def set_budget(limit_inr: int) -> str:
    """Set the total trip budget in INR. Call once at the start when known."""
    return _wrap(await call_mcp("set_budget", {
        "trip_id": _trip_id.get(),
        "limit_inr": int(limit_inr),
    }))


@tool
async def check_budget(proposed_amount_inr: int, category: str) -> str:
    """Check whether a proposed spend fits the remaining budget.

    Returns {ok, remaining_inr, ...}. Call BEFORE commit_spend.
    """
    return _wrap(await call_mcp("check_budget", {
        "trip_id": _trip_id.get(),
        "proposed_amount_inr": int(proposed_amount_inr),
        "category": category,
    }))


@tool
async def commit_spend(amount_inr: int, category: str, description: str) -> str:
    """Commit a spend against the trip's budget AFTER check_budget returned ok."""
    return _wrap(await call_mcp("commit_spend", {
        "trip_id": _trip_id.get(),
        "amount_inr": int(amount_inr),
        "category": category,
        "description": description,
    }))


# ─── payment tools (direct MCP to mcp-payment) ───────────────────────────

@tool
async def authorize_payment(amount_inr: int, vendor: str, items_json: str) -> str:
    """Authorize a payment (puts a hold but DOES NOT charge). Returns {auth_id, ...}.

    On the very next step, call capture_payment(auth_id=<the EXACT auth_id
    string from this tool's returned JSON>). Pass the auth_id through
    verbatim. DO NOT invent a placeholder, do not normalise the format ·
    capture only accepts the literal opaque token this tool returned.

    Args:
        amount_inr: Total to authorize.
        vendor: Display name for the charge.
        items_json: JSON-encoded list of {label, amount_inr} line items.
    """
    try:
        items = json.loads(items_json)
    except (ValueError, TypeError):
        items = []
    return _wrap(await call_mcp("authorize", {
        "amount_inr": int(amount_inr),
        "vendor": vendor,
        "items": items,
    }))


@tool
async def capture_payment(auth_id: str) -> str:
    """IRREVERSIBLY charge a previously-authorized payment.

    auth_id MUST be the exact opaque string returned by the preceding
    authorize_payment call. NEVER pass a placeholder like AUTH-PLACEHOLDER,
    AUTH-XXXX, <auth_id>, or any invented value · capture will fail with
    "auth_id not found". Read the auth_id out of authorize_payment's tool
    result JSON and pass it through character-for-character.

    Call this on the very next step after authorize_payment returns. The
    PROTOCOL (LangGraph interrupt_before) pauses execution BEFORE this
    runs and presents the user with an approval modal · you do not need
    to poll the user for consent yourself. On approve → capture fires.
    On cancel → a synthetic decline is injected and you'll see it on
    your next turn.
    """
    return _wrap(await call_mcp("capture", {"auth_id": auth_id}))


# ─── calendar tools (direct MCP to mcp-calendar) ─────────────────────────

@tool
async def add_calendar_events(events_json: str) -> str:
    """Add a list of events to the user's Google Calendar.

    Args:
        events_json: JSON-encoded list of {title, date (YYYY-MM-DD), time (HH:MM)}.
    """
    try:
        events = json.loads(events_json)
    except (ValueError, TypeError):
        events = []
    return _wrap(await call_mcp("add_events", {
        "trip_id": _trip_id.get(),
        "events": events,
    }))


# ─── bound list for the LLM + name set for SSE-event filter ──────────────

TOOLS = [
    delegate_to_research_agent,       # Phase 4 · real A2A to research-agent
    set_budget, check_budget, commit_spend,
    delegate_to_flight_agent,         # Phase 3 · real A2A to flight-agent
    delegate_to_hotel_agent,          # Phase 4 · real A2A to hotel-agent
    delegate_to_itinerary_agent,      # Phase 4 · real A2A to itinerary-agent
    delegate_to_critic_agent,         # Phase 4 · real A2A to critic-agent
    delegate_to_todo_agent,           # Phase 4 · real A2A to todo-agent
    authorize_payment, capture_payment,
    add_calendar_events,
]

# Names of OUTER @tools the LLM is allowed to call. The SSE producer
# filter (sse.py) uses this set to suppress events for the nested MCP
# tool calls that happen inside these wrappers.
PLANNER_TOOL_NAMES: set[str] = {t.name for t in TOOLS}
