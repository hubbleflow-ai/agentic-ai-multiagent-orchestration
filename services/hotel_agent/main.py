"""hotel-agent · LangGraph supervisor over mcp-hotel, exposed via A2A.

Phase 4 of the Session 6 refactor.  Mirrors flight-agent.

  Planner  ──(A2A: natural-language brief)──▶  hotel-agent (this service)
                                                       │
                                                       ▼
                                          LangGraph: model + ToolNode
                                                       │
                                       ┌───────────────┴───────────────┐
                                       │                               │
                                       ▼                               ▼
                              search_hotels (MCP)              hold_hotel (MCP)
                                       │                               │
                                       └────────► mcp-hotel  ◄─────────┘

Endpoints exposed by `add_a2a_routes_to_fastapi`:
  GET  /.well-known/agent-card.json   · A2A Agent Card discovery
  POST /                              · A2A JSON-RPC 2.0 (SendMessage, etc.)
  GET  /health                        · docker-compose health probe (ours)
"""

from __future__ import annotations

import logging
import os

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils import TransportProtocol
from fastapi import FastAPI
from langchain_core.messages import SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from shared.agent_template import LangGraphA2AExecutor
from shared.observability import setup_observability

setup_observability("hotel-agent")
log = logging.getLogger(__name__)

# ─── config ──────────────────────────────────────────────────────────────

MCP_HOTEL_URL = os.environ.get("MCP_HOTEL_URL", "http://mcp-hotel:9012/mcp")
PUBLIC_URL = os.environ.get("HOTEL_AGENT_PUBLIC_URL", "http://hotel-agent:8011/")


# ─── prompt ──────────────────────────────────────────────────────────────

_SYSTEM = """You are hotel-agent · a focused sub-agent specialising in hotel discovery
and booking holds for an Indian travel concierge.

You have these MCP tools available:
  - search_hotels(city, check_in, check_out, pax, max_per_night_inr?)
        → 12 realistic hotel options sorted by rating descending
  - hold_hotel(hotel_id, check_in, check_out)
        → places a 30-minute hold on one option

## DECIDE THE BRIEF TYPE FIRST · SEARCH or HOLD

Every brief is one of two types. Classify it before doing anything else.

**SEARCH brief** · examples:
  "Find me a hotel in Tokyo from Oct 15 to Oct 19 for 2 adults"
  "Search hotels in Goa 26-30 Dec, 2 adults, under ₹10k per night"
  "What are the hotel options for Paris next month?"

**HOLD brief** · examples:
  "Hold the Park Hyatt Shinjuku from Oct 15 to Oct 19"
  "Book the HTL-TOK-003 for check-in 2026-10-15, check-out 2026-10-19"
  "Lock in the Trunk Hotel for those dates"

## SEARCH FLOW
  1. Parse city, check_in, check_out, pax (and max_per_night_inr if user
     mentioned a per-night budget). If something essential is missing,
     reply with EXACTLY what you need clarified · do NOT guess silently.
  2. CALL search_hotels · this is MANDATORY. Do NOT skip the tool call.
     Do NOT claim to have searched without calling it · the user's UI
     only renders cards from the actual tool output.
  3. From the 12 options, pick a single recommendation and explain WHY in
     one short paragraph. Lean toward:
       - higher rating > lower rating
       - under-budget > over-budget (if the brief mentions one)
       - central / convenient neighborhood
       - well-known reputable chain or boutique that fits the vibe
     Mention 2 backup options briefly (name + per-night price).

## HOLD FLOW
  1. Extract the hotel_id (e.g. "HTL-TOK-001") OR the hotel name from the
     brief. Extract check_in and check_out dates.
  2. CALL hold_hotel(hotel_id=..., check_in=..., check_out=...) · this is
     MANDATORY. Do NOT skip the tool call. Do NOT write "I've placed the
     hold" or "booked" or anything similar WITHOUT first calling the
     tool. The user's UI only shows the booking as confirmed when the
     actual MCP hold_hotel tool fires and returns a hold_id.
  3. After the tool returns, report the hold_id and expiry in 1-2 sentences.

## CRITICAL · NEVER FABRICATE TOOL RESULTS
- NEVER claim a hold was placed without calling hold_hotel.
- NEVER claim to have searched hotels without calling search_hotels.
- NEVER invent hotel IDs, prices, hold_ids, or hotel names · they must
  come from the actual tool returns.
- If you find yourself about to write "I've held the hotel" or "I found
  these options" · STOP, and call the tool first.

## REPLY STYLE
Concise natural language · 4-6 sentences total, no markdown headings or
long bullet lists.
"""


# ─── graph (built lazily on first request) ───────────────────────────────

async def _build_graph():
    log.info("hotel-agent · loading MCP tools from %s", MCP_HOTEL_URL)
    mcp_client = MultiServerMCPClient({
        "hotel": {
            "transport": "streamable_http",
            "url": MCP_HOTEL_URL,
        }
    })
    tools = await mcp_client.get_tools()
    log.info("hotel-agent · loaded %d tools: %s", len(tools), [t.name for t in tools])

    llm = ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        temperature=0.2,
    ).bind_tools(tools)

    async def model_node(state: MessagesState):
        msgs = [SystemMessage(content=_SYSTEM), *state["messages"]]
        response = await llm.ainvoke(msgs)
        return {"messages": [response]}

    def should_continue(state: MessagesState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(MessagesState)
    graph.add_node("model", model_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")

    return graph.compile()


# ─── A2A Agent Card ──────────────────────────────────────────────────────

agent_card = AgentCard(
    # Top-level description is the EXACT text from the OLD
    # delegate_to_hotel_agent docstring in planner's tools.py.
    name="hotel-agent",
    description=(
        "Delegate a hotel-related task to the hotel specialist sub-agent (A2A).\n\n"
        "Same pattern as delegate_to_flight_agent. ONE city stay per call ·\n"
        "NEVER bundle multiple stays. UI renders hotel cards from the\n"
        "search_hotels artifact."
    ),
    version="1.0.0",
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    supported_interfaces=[
        AgentInterface(
            url=PUBLIC_URL,
            protocol_binding=TransportProtocol.JSONRPC.value,
            protocol_version="1.0",
        ),
    ],
    capabilities=AgentCapabilities(streaming=False),
    # Two atomic skills, mirroring flight-agent's shape.
    skills=[
        AgentSkill(
            id="search_hotels",
            name="Search hotels",
            description=(
                "Search-and-recommend mode. I return 12 hotel options for "
                "ONE city + check-in/out window, ranked, with a recommended "
                "pick and 2 backups."
            ),
            tags=["hotel", "search"],
            examples=[
                "Find me a hotel in Tokyo from 2026-10-15 to 2026-10-19 for 2 adults, under ₹15k per night",
                "Search hotels in Paris from 2027-04-15 to 2027-04-22 for 2 adults, walking distance from the Louvre",
            ],
        ),
        AgentSkill(
            id="hold_hotel",
            name="Hold a specific hotel",
            description=(
                "Hold mode. Place a 30-minute hold on a specific hotel the "
                "user picked from a previous search recommendation. "
                "Returns {hold_id, expires_at, price_total_inr}."
            ),
            tags=["hotel", "hold", "booking"],
            examples=[
                "Hold the Park Hyatt Shinjuku from 2026-10-15 to 2026-10-19",
                "Hold the Hôtel Plaza Athénée you recommended from Apr 15 to Apr 22",
            ],
        ),
    ],
)


# ─── FastAPI app + A2A routes ────────────────────────────────────────────

executor = LangGraphA2AExecutor(_build_graph, agent_name="hotel-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="hotel-agent", version="1.0.0")

add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


# ─── health probe for docker-compose ─────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "hotel-agent"}
