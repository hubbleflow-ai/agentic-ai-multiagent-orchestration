"""todo-agent · LangGraph A2A sub-agent for pre-trip todos.

Phase 4 closer. Same pattern as itinerary-agent · the LLM parses the
brief and calls mcp-trip-state.create_todos to produce the structured
list. The todos come back as an A2A Artifact for the supervisor to
forward to the frontend.

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

setup_observability("todo-agent")
log = logging.getLogger(__name__)

MCP_TRIP_STATE_URL = os.environ.get("MCP_TRIP_STATE_URL", "http://mcp-trip-state:9015/mcp")
PUBLIC_URL = os.environ.get("TODO_AGENT_PUBLIC_URL", "http://todo-agent:8016/")


_SYSTEM = """You are todo-agent · a focused sub-agent that produces pre-trip
todos (passport, visa, insurance, bank notification, forex, packing,
check-in) for an Indian travel concierge.

You have one MCP tool available (from mcp-trip-state):
  - create_todos(destination, depart_date)
        → TodosResponse {count, todos: [{id, text, priority, due_date}]}

## FLOW
  1. Parse destination and depart_date (YYYY-MM-DD) from the brief.
     If depart_date is missing, ask for it · don't guess.
  2. CALL create_todos · MANDATORY. Do NOT skip. Do NOT write a todo
     list yourself in prose; the MCP tool produces the structured list
     that the UI renders as todo cards.
  3. After the tool returns, reply in ONE concise sentence
     (e.g. "Created 7 pre-trip todos · highest-priority is passport
     check 45 days out").

## CRITICAL · NEVER FABRICATE
- NEVER write a list of todos in your text reply without calling the
  tool · the UI cards only populate from the MCP artifact.
- NEVER invent due dates or todo text · they come from the tool return.

## REPLY STYLE
ONE sentence. Indian English. No markdown, no list enumeration.
"""


async def _build_graph():
    log.info("todo-agent · loading MCP tools from %s", MCP_TRIP_STATE_URL)
    mcp_client = MultiServerMCPClient({
        "trip_state": {
            "transport": "streamable_http",
            "url": MCP_TRIP_STATE_URL,
        }
    })
    tools = await mcp_client.get_tools()
    # Only expose todo-related tools to this agent.
    tools = [t for t in tools if "todo" in t.name]
    log.info("todo-agent · loaded %d tools: %s", len(tools), [t.name for t in tools])

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
    name="todo-agent",
    description=(
        "Pre-trip todo specialist. Generates the standard prep checklist "
        "(passport, visa, insurance, bank, forex, packing, check-in) with "
        "due dates anchored to departure."
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
            id="create_pretrip_todos",
            name="Create pre-trip todos",
            description=(
                "Generate a standard pre-trip checklist with due dates "
                "anchored to the departure date."
            ),
            tags=["todo", "travel", "planning"],
            examples=[
                "Create pre-trip todos for Tokyo with departure on 2026-10-15.",
            ],
        )
    ],
)


executor = LangGraphA2AExecutor(_build_graph, agent_name="todo-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="todo-agent", version="1.0.0")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "todo-agent"}
