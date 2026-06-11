"""flight-agent · LangGraph supervisor over mcp-airline, exposed via A2A.

Phase 2 of the Session 6 refactor.  Architecture:

  Planner  ──(A2A: natural-language brief)──▶  flight-agent (this service)
                                                       │
                                                       ▼
                                          LangGraph: model + ToolNode
                                                       │
                                       ┌───────────────┴───────────────┐
                                       │                               │
                                       ▼                               ▼
                              search_flights (MCP)             hold_flight (MCP)
                                       │                               │
                                       └────────► mcp-airline ◄────────┘

What this service does:
  1. Connects to mcp-airline at startup · loads its tools as LangChain Tools.
  2. Builds a LangGraph StateGraph(MessagesState) · model node + ToolNode.
  3. On each A2A message, the graph reasons over the brief, calls
     search_flights, picks a recommendation, optionally calls hold_flight.
  4. Final assistant message is emitted as the A2A response via TaskUpdater.

What this service does NOT do:
  - It does NOT generate flight inventory itself · that's mcp-airline's job.
  - It does NOT persist conversation across A2A calls · each request is
    independent (planner owns the multi-turn state).

Endpoints exposed by `add_a2a_routes_to_fastapi`:
  GET  /.well-known/agent-card.json   · A2A Agent Card discovery
  POST /                              · A2A JSON-RPC 2.0 (message/send, etc.)
  POST /v1/...                        · REST routes (per SDK convention)
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

setup_observability("flight-agent")
log = logging.getLogger(__name__)

# ─── config ──────────────────────────────────────────────────────────────

MCP_AIRLINE_URL = os.environ.get("MCP_AIRLINE_URL", "http://mcp-airline:9011/mcp")

# PUBLIC_URL is what the Agent Card advertises to A2A clients. Inside the
# Docker compose network this is the service DNS name · outside (e.g. from
# the host) it is localhost. The card always advertises the in-cluster URL
# since the planner sits inside the network.
PUBLIC_URL = os.environ.get("FLIGHT_AGENT_PUBLIC_URL", "http://flight-agent:8010/")


# ─── prompt ──────────────────────────────────────────────────────────────

_SYSTEM = """You are flight-agent · a focused sub-agent specialising in flight discovery
and booking holds for an Indian travel concierge.

You have these MCP tools available:
  - search_flights(origin, destination, depart_date, pax, return_date?)
        → 12 realistic flight options sorted ascending by price_total_inr
  - hold_flight(flight_id, pax)
        → places a 30-minute hold on one option

## DECIDE THE BRIEF TYPE FIRST · SEARCH or HOLD

Every brief is one of two types. Classify it before doing anything else.

**SEARCH brief** · examples:
  "Find me a flight from BLR to NRT on 2026-10-15 for 2 adults"
  "Search BLR → SYD for Dec 25, 2 adults, under ₹1L"
  "What are the options for Bengaluru to Tokyo Oct 15?"

**HOLD brief** · examples:
  "Hold the Malaysia Airlines MH192 for 2 adults"
  "Book the IndiGo 6E2104 flight"
  "Lock in the Air India AI143"
  "Place a hold on Thai TG326"

## SEARCH FLOW
  1. Parse origin, destination, depart_date, pax (and return_date if
     round-trip). If something essential is missing, reply with EXACTLY
     what you need clarified · do NOT guess silently.
  2. CALL search_flights · this is MANDATORY. Do NOT skip the tool call.
     Do NOT claim to have searched without calling it · the user's UI
     only renders cards from the actual tool output.
  3. From the 12 options, pick a single recommendation and explain WHY in
     one short paragraph. Lean toward:
       - non-stop > 1-stop > 2-stop
       - under-budget > over-budget (if the brief mentions one)
       - reputable carrier > budget carrier for international long-haul
     Mention 2 backup options briefly (carrier + price).

## HOLD FLOW
  1. Extract the flight_id from the brief (e.g. "MH192", "AI314", "6E2104").
     The pax count is in the brief; if not, default to 2.
  2. CALL hold_flight(flight_id=..., pax=...) · this is MANDATORY. Do NOT
     skip the tool call. Do NOT write "I've placed the hold" or "hold
     confirmed" or anything similar WITHOUT first calling the tool. The
     user's UI only shows the booking as confirmed when the actual MCP
     hold_flight tool fires and returns a hold_id.
  3. After the tool returns, report the hold_id and expiry in 1-2 sentences.

## CRITICAL · NEVER FABRICATE TOOL RESULTS
- NEVER claim a hold was placed without calling hold_flight.
- NEVER claim to have searched flights without calling search_flights.
- NEVER invent flight IDs, prices, hold_ids, or airline names · they must
  come from the actual tool returns.
- If you find yourself about to write "I've held the flight" or
  "I found these options" · STOP, and call the tool first.

## REPLY STYLE
Concise natural language · 4-6 sentences total, no markdown headings or
long bullet lists.
"""


# ─── graph (built lazily on first request because MCP load is async) ─────

async def _build_graph():
    log.info("flight-agent · loading MCP tools from %s", MCP_AIRLINE_URL)
    mcp_client = MultiServerMCPClient({
        "airline": {
            "transport": "streamable_http",
            "url": MCP_AIRLINE_URL,
        }
    })
    tools = await mcp_client.get_tools()
    log.info("flight-agent · loaded %d tools: %s", len(tools), [t.name for t in tools])

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
    # Top-level description is the EXACT text that used to be the
    # planner's hand-written delegate_to_flight_agent docstring · moved
    # here so the planner now DISCOVERS this guidance from the card
    # instead of hardcoding it. Do not add agent-internal implementation
    # details (e.g. "mcp-airline") here · the supervisor doesn't need
    # to know which MCP the agent uses internally.
    name="flight-agent",
    description=(
        "Delegate a flight-related task to the flight specialist sub-agent (A2A).\n\n"
        "Returns a JSON object (encoded as string) with two top-level fields:\n\n"
        '  "text"      · the sub-agent\'s natural-language reply (recommendation\n'
        "                + backups, or hold confirmation). PARAPHRASE this for\n"
        "                the user; do NOT enumerate option lists or prices.\n\n"
        '  "artifacts" · structured outputs · UI renders flight cards from these.\n\n'
        "Pattern of use:\n"
        "    1. First call: search-and-recommend brief\n"
        "    2. Surface the recommendation, wait for user pick\n"
        "    3. Second call: hold brief\n"
        "    4. Then check_budget + commit_spend"
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
    # Two atomic skills, each with its OWN example briefs. The planner's
    # dynamic_a2a_tools.py concatenates these (skill description +
    # examples list) onto the top-level description above to form the
    # full tool docstring the LLM sees.
    skills=[
        AgentSkill(
            id="search_flights",
            name="Search flights",
            description=(
                "Search-and-recommend mode. I return 12 flight options for "
                "ONE route + date, ranked, with a recommended pick and 2 backups."
            ),
            tags=["flight", "search"],
            examples=[
                "Search BLR to NRT on 2026-10-15 for 2 adults, prefer non-stop, under ₹1.5L per leg",
                "Find a non-stop BLR to CDG flight on 2027-04-15 for 2 adults under ₹1L per leg",
            ],
        ),
        AgentSkill(
            id="hold_flight",
            name="Hold a specific flight",
            description=(
                "Hold mode. Place a 30-minute hold on a specific flight the "
                "user picked from a previous search recommendation. "
                "Returns {hold_id, expires_at, price_total_inr}."
            ),
            tags=["flight", "hold", "booking"],
            examples=[
                "Hold the JAL JL754 you recommended for 2 adults",
                "Hold the Air India AI314 non-stop from your last recommendation",
            ],
        ),
    ],
)


# ─── FastAPI app + A2A routes ────────────────────────────────────────────

executor = LangGraphA2AExecutor(_build_graph, agent_name="flight-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="flight-agent", version="1.0.0")

add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


# ─── health probe for docker-compose ─────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "flight-agent"}
