"""planner · LangChain @tool wrappers for the planner's MCP-backed tools.

Post dynamic-A2A-discovery migration · all 6 delegate_to_<agent> tools
moved to services/planner/dynamic_a2a_tools.py and are built at startup
from each sub-agent's Agent Card. This file now holds ONLY the 6
MCP-backed tools the planner calls DIRECTLY:

  - set_budget / check_budget / commit_spend   (mcp-trip-state)
  - authorize_payment / capture_payment        (mcp-payment)
  - add_calendar_events                        (mcp-calendar)

These tools stay hand-wrapped (not auto-discovered) for three reasons:

  1. trip_id ContextVar injection · the LLM shouldn't see/pass trip_id;
     each call reads it from the ambient session ContextVar.
  2. Name remapping · the @tool name (authorize_payment) differs from
     the MCP tool name (authorize) for LLM clarity.
  3. Type coercion · the LLM sometimes sends floats where ints are
     required; explicit int() casts here prevent MCP schema errors.

graph.py merges this STATIC_TOOLS list with the dynamic A2A tools list
to form the full 12-tool TOOLS catalogue bound to Gemini.

Helpers:
  - `_trip_id` ContextVar · carries the session id so MCP tools can
    pass it through without the LLM knowing it exists.
  - `_wrap` · JSON-encodes dict/list results because @tool must return
    a string.
"""

from __future__ import annotations

import json
from contextvars import ContextVar

from langchain_core.tools import tool

from services.planner.mcp_client import call_mcp


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

# STATIC_TOOLS · the 6 MCP-backed tools. The 6 A2A delegate_to_<agent>
# tools come from services.planner.dynamic_a2a_tools.build_all_a2a_tools()
# at startup. graph.py combines both into the full 12-tool TOOLS list.
STATIC_TOOLS = [
    set_budget, check_budget, commit_spend,
    authorize_payment, capture_payment,
    add_calendar_events,
]

# Backward-compat alias · prefer importing TOOLS from services.planner.graph
# (which includes the dynamic A2A tools). This alias is the STATIC subset.
TOOLS = STATIC_TOOLS

# Names of OUTER @tools the LLM is allowed to call. The SSE producer
# filter (sse.py) uses this to suppress events for nested MCP tool calls.
# graph.py .add()'s every dynamic A2A tool's name into this set at startup.
PLANNER_TOOL_NAMES: set[str] = {t.name for t in STATIC_TOOLS}
