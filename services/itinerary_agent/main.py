"""itinerary-agent · LangGraph A2A sub-agent for day-by-day itineraries.

Phase 4 closer. Mirrors flight-agent / hotel-agent · the LLM reasons over
the brief and calls mcp-trip-state's build_itinerary / revise_itinerary
tools to do the actual structured generation. The structured day plan
comes back as an A2A Artifact (DataPart) that the supervisor forwards to
the frontend for rendering as day cards.

Endpoints (via add_a2a_routes_to_fastapi):
  GET  /.well-known/agent-card.json
  POST /                              · A2A JSON-RPC 2.0
  GET  /health
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

setup_observability("itinerary-agent")
log = logging.getLogger(__name__)

MCP_TRIP_STATE_URL = os.environ.get("MCP_TRIP_STATE_URL", "http://mcp-trip-state:9015/mcp")
PUBLIC_URL = os.environ.get("ITINERARY_AGENT_PUBLIC_URL", "http://itinerary-agent:8012/")


_SYSTEM = """You are itinerary-agent · a focused sub-agent that produces day-by-day
travel itineraries for an Indian travel concierge.

You have these MCP tools available (from mcp-trip-state):
  - build_itinerary(city, days, interests?, hotel_neighborhood?)
        → full structured ItineraryResponse {city, days[]}
  - revise_itinerary(itinerary_json, critique)
        → revised ItineraryResponse with notes on changed days

## DECIDE THE BRIEF TYPE FIRST · BUILD or REVISE

**BUILD brief** · examples:
  "Build a 4-day Tokyo itinerary for a foodie traveller, hotel in Shinjuku"
  "Create a 7-day Bali plan, interests: beach + culture"

**REVISE brief** · examples:
  "Revise this itinerary based on critic feedback: {...} CRITIQUE: Day 1 too packed"
  "Apply this critique to the itinerary: ..."

## BUILD FLOW
  1. Parse city, days, interests (list), hotel_neighborhood (if given).
     If days or city missing, reply asking for clarification.
  2. CALL build_itinerary · MANDATORY. Do NOT skip. Do NOT fabricate a
     day-by-day plan yourself; the MCP tool is the ONLY way to produce
     the structured plan the UI renders as cards.
  3. After the tool returns, reply in ONE concise sentence
     (e.g. "Here's the 4-day Tokyo plan · let me know if any day needs
     reshuffling"). DO NOT enumerate Day 1 / Day 2 in your reply ·
     the UI already shows the day-by-day cards.

## REVISE FLOW
  1. Extract the current itinerary JSON AND the critique text from the
     brief.
  2. CALL revise_itinerary(itinerary_json, critique) · MANDATORY.
  3. Reply in ONE concise sentence
     (e.g. "Updated · trimmed Day 1 and rebalanced Day 3 per the
     critique").

## CRITICAL · NEVER FABRICATE
- NEVER write a day-by-day plan in your text reply without calling the
  tool · the UI cards only populate from the MCP artifact.
- NEVER invent place names, times, or itinerary IDs · they come from
  the tool return.
- If you find yourself about to write "Day 1 · Morning · Visit..." STOP,
  and call the tool first.

## REPLY STYLE
ONE sentence. Indian English. No markdown, no day enumeration.
"""


async def _build_graph():
    log.info("itinerary-agent · loading MCP tools from %s", MCP_TRIP_STATE_URL)
    mcp_client = MultiServerMCPClient({
        "trip_state": {
            "transport": "streamable_http",
            "url": MCP_TRIP_STATE_URL,
        }
    })
    tools = await mcp_client.get_tools()
    # Only expose itinerary-related tools to this agent (mcp-trip-state
    # also carries create_todos which is todo-agent's territory).
    tools = [t for t in tools if "itinerary" in t.name]
    log.info("itinerary-agent · loaded %d tools: %s", len(tools), [t.name for t in tools])

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


agent_card = AgentCard(
    name="itinerary-agent",
    description=(
        "Day-by-day itinerary specialist. Builds a structured plan via "
        "mcp-trip-state.build_itinerary or revises one based on critic feedback."
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
    skills=[
        AgentSkill(
            id="build_or_revise_itinerary",
            name="Build or revise itinerary",
            description=(
                "Build a fresh day-by-day plan or revise an existing one "
                "based on critic feedback."
            ),
            tags=["itinerary", "travel", "planning"],
            examples=[
                "Build a 4-day Tokyo itinerary for a foodie traveller, hotel in Shinjuku.",
                "Revise this itinerary based on critic feedback: {...}",
            ],
        )
    ],
)


executor = LangGraphA2AExecutor(_build_graph, agent_name="itinerary-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="itinerary-agent", version="1.0.0")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "itinerary-agent"}
