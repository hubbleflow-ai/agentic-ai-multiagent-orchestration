"""critic-agent · text-only A2A sub-agent for artifact review (Reflexion pattern).

Phase 4 of the Session 6 refactor. No MCP tools · this is a pure LLM
agent that takes an artifact (typically an itinerary JSON) and a brief
context, and returns a critique: what's good, what's risky, concrete
suggestions to revise.

Used by the supervisor's plan-then-critique loop:
  1. itinerary-agent produces day-by-day plan
  2. critic-agent reviews it
  3. supervisor sends critique back to itinerary-agent for revision
  4. supervisor surfaces the (possibly revised) plan to the user

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
from langgraph.graph import END, START, MessagesState, StateGraph

from shared.agent_template import LangGraphA2AExecutor
from shared.observability import setup_observability

setup_observability("critic-agent")
log = logging.getLogger(__name__)

PUBLIC_URL = os.environ.get("CRITIC_AGENT_PUBLIC_URL", "http://critic-agent:8015/")


_SYSTEM = """You are critic-agent · a sub-agent that reviews trip-planning
artifacts for an Indian travel concierge. You operate on artifacts
embedded in the brief (typically itineraries serialised as JSON, but
also sometimes flight/hotel choices).

Your job is to flag issues before the user sees the artifact. Be specific
and actionable. Common failure modes to watch for:

For itineraries:
  - Day 1 is too packed (arrival/jet lag · keep it light, ≤3 items)
  - Any day has >5 items (burnout risk)
  - Tight transit windows between far-apart spots in the same day
  - Missing meal slots (breakfast/lunch/dinner mostly accounted for?)
  - Heritage / cultural sites on closure days (e.g. many Tokyo museums
    closed on Mondays · Louvre closed Tuesdays)
  - No buffer time around international flight arrival/departure days

For flights:
  - Connection time too short for international transit (<2h is risky)
  - Overnight red-eye that wrecks day 1

For hotels:
  - Neighborhood mismatch with declared interests
  - Distance from the day's planned activities

## REPLY SHAPE
ONE concise paragraph (4-7 sentences):
  - Open with verdict: "Looks solid" OR "Two issues worth flagging" OR
    "Major concern · suggest revising"
  - State each issue in plain English with the specific day/leg/item
  - End with ONE concrete suggested fix per issue · the supervisor will
    pass your suggestions to itinerary-agent or flight-agent for revision.

If everything is fine, say so plainly · don't manufacture issues. If
you're flagging issues, be specific (which day, which item).

Style: Indian English, no markdown bullet lists, no headings, no emojis.
"""


async def _build_graph():
    llm = ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        temperature=0.3,
    )

    async def model_node(state: MessagesState):
        msgs = [SystemMessage(content=_SYSTEM), *state["messages"]]
        response = await llm.ainvoke(msgs)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


agent_card = AgentCard(
    name="critic-agent",
    description=(
        "Reflexion-style critic. Reviews trip artifacts (typically itineraries) "
        "for risks before the user sees them. Returns a concise verdict + "
        "specific issues + concrete suggested fixes."
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
            id="review_artifact",
            name="Review trip artifact",
            description=(
                "Critique an itinerary / flight choice / hotel choice for "
                "issues. Returns verdict + specific issues + suggested fixes."
            ),
            tags=["critic", "review", "reflexion"],
            examples=[
                "Review this 4-day Tokyo itinerary: {\"days\":[...]}. The user lands at 06:00 Day 1.",
            ],
        )
    ],
)


executor = LangGraphA2AExecutor(_build_graph, agent_name="critic-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="critic-agent", version="1.0.0")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "critic-agent"}
