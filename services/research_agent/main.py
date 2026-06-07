"""research-agent · text-only A2A sub-agent for destination research.

Phase 4 of the Session 6 refactor. No MCP tools · this is a pure LLM
agent that takes a natural-language brief about a destination and returns
visa info, weather, neighborhoods, must-see, and gotchas as a single
prose reply.

For the cohort demo we don't wire it to a real web search · Gemini's
training data is sufficient for the canonical city set (Tokyo, Goa,
Bali, Sydney, Paris, etc.). Phase 8 polish can add a tavily-search MCP
server if needed.

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

setup_observability("research-agent")
log = logging.getLogger(__name__)

PUBLIC_URL = os.environ.get("RESEARCH_AGENT_PUBLIC_URL", "http://research-agent:8018/")


_SYSTEM = """You are research-agent · a focused sub-agent that briefs an Indian
travel concierge on destinations. You DO NOT have web search · use your
training knowledge. The supervisor calls you BEFORE flight/hotel
decisions, so your job is to anchor those subsequent picks.

When given a city + (optional) traveller origin + interests, reply with a
single concise paragraph (5-8 sentences) covering, in this order:

  1. Visa requirements for Indian passport holders (eVisa, on-arrival, or
     embassy · single/multiple entry · processing time · validity)
  2. Best months to visit + weather characteristics in the user's date
     window if provided
  3. Top 3-4 distinct neighborhoods worth mentioning (with vibes: nightlife
     vs culture vs luxury vs beach etc.)
  4. 3-5 must-see spots (mix iconic + local-favorite)
  5. 2-3 gotchas Indian travellers should know (cash vs card, transit IC
     card, dress code, food allergens, language, etc.)

Style:
  - Indian English, INR for any prices mentioned
  - One concise paragraph · do NOT use markdown bullet lists, do NOT use
    headings. The supervisor will paraphrase for the user.
  - Do NOT invent specific prices or visa fee amounts (unless very common
    knowledge). Stick to qualitative guidance.
  - 5-8 sentences total. Short and useful.

NEVER refuse a city, even if obscure · share what you know.
"""


async def _build_graph():
    llm = ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        temperature=0.4,
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
    name="research-agent",
    description=(
        "Destination research specialist for an Indian travel concierge. "
        "Given a city + dates + interests, returns visa info, best months, "
        "neighborhoods, must-see spots, and gotchas in one concise briefing."
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
            id="research_destination",
            name="Research destination",
            description=(
                "Brief the supervisor on a destination: visa, weather, "
                "neighborhoods, must-see, gotchas · one concise paragraph."
            ),
            tags=["research", "travel", "destination"],
            examples=[
                "Tell me about Tokyo for an Indian traveller visiting in October.",
                "What should we know about Bali for a 7-day trip in December?",
            ],
        )
    ],
)


executor = LangGraphA2AExecutor(_build_graph, agent_name="research-agent")
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

app = FastAPI(title="research-agent", version="1.0.0")
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "research-agent"}
