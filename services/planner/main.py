"""planner · FastAPI app + HTTP routes.

Thin orchestrator · all the heavy lifting lives in sibling modules:

    config.py      env vars + URL constants
    prompt.py      SYSTEM_PROMPT (single source of truth)
    tts.py         Cloud TTS + Gemini TTS streaming pipeline
    mcp_client.py  lazy MCP tool registry + call_mcp() normaliser
    tools.py       LangChain @tool wrappers + bound TOOLS list
    graph.py       LangGraph + Gemini LLM + Phase 7 HITL split
    sse.py         the SSE event producer (shared by all 3 stream endpoints)

This file only knows about FastAPI: the app instance, CORS, the route
handlers, and Pydantic request models. Nothing more.

Endpoints:
    GET  /health         · health probe + arch summary
    POST /agent/reset    · drop a session's checkpointer history
    POST /agent/stream   · stream one turn (SSE)
    POST /agent/resume   · resume a paused HITL gate (SSE · Phase 7)
    POST /agent/cancel   · cancel a paused HITL gate (SSE · Phase 7)
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from services.planner.graph import CAPTURE_NODE, TOOLS, agent, checkpointer
from services.planner.sse import stream_agent_events
from shared.observability import setup_observability

setup_observability("planner")

log = logging.getLogger(__name__)

app = FastAPI(title="planner", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── request shapes ──────────────────────────────────────────────────────

class AgentRequest(BaseModel):
    session_id: str
    message: str


class ResetRequest(BaseModel):
    session_id: str


class ResumeRequest(BaseModel):
    session_id: str


class CancelRequest(BaseModel):
    session_id: str


# ─── routes ──────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "planner",
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "tools": len(TOOLS),
        "a2a_subagents": 6,    # flight, hotel, research, critic, itinerary, todo
        "mcp_servers": 5,      # airline, hotel, trip-state, payment, calendar
    }


@app.post("/agent/reset")
async def agent_reset(req: ResetRequest) -> dict:
    """Drop a session's history. Next /agent/stream starts fresh."""
    try:
        await checkpointer.aput(  # type: ignore[arg-type]
            {"configurable": {"thread_id": req.session_id}},
            checkpoint=None,  # type: ignore[arg-type]
            metadata={},
            new_versions={},
        )
    except Exception:
        pass
    return {"ok": True, "session_id": req.session_id}


@app.post("/agent/stream")
async def agent_stream(req: AgentRequest):
    """Stream one agent turn (one user message) as SSE."""
    log.info("planner.agent.run session=%s msg=%s", req.session_id, req.message[:80])
    return EventSourceResponse(stream_agent_events(
        agent_input={"messages": [HumanMessage(content=req.message)]},
        session_id=req.session_id,
    ))


@app.post("/agent/resume")
async def agent_resume(req: ResumeRequest):
    """Resume a graph that paused at the capture_payment HITL gate.

    Frontend POSTs here after the user clicks Approve on the modal · the
    graph picks up at the capture_payment node and runs the real charge,
    then continues through calendar + todos + final summary.
    """
    log.info("planner.agent.resume session=%s", req.session_id)
    config = {"configurable": {"thread_id": req.session_id}}
    state = await agent.aget_state(config)
    if CAPTURE_NODE not in (state.next or ()):
        raise HTTPException(409, "no interrupted run to resume")
    return EventSourceResponse(stream_agent_events(
        agent_input=None,   # None = resume from checkpoint
        session_id=req.session_id,
    ))


@app.post("/agent/cancel")
async def agent_cancel(req: CancelRequest):
    """Cancel a graph paused at capture_payment · inject a synthetic decline.

    Frontend POSTs here after the user clicks Cancel on the modal. We
    inject a ToolMessage that LOOKS like capture_payment returned a
    'declined' result, then resume the graph · the model sees the
    declined result and wraps up the conversation cleanly.
    """
    log.info("planner.agent.cancel session=%s", req.session_id)
    config = {"configurable": {"thread_id": req.session_id}}
    state = await agent.aget_state(config)
    if CAPTURE_NODE not in (state.next or ()):
        raise HTTPException(409, "no interrupted run to cancel")

    messages = state.values.get("messages", []) if state.values else []
    last_ai = messages[-1] if messages else None
    pending = None
    for tc in getattr(last_ai, "tool_calls", None) or []:
        if tc.get("name") == CAPTURE_NODE:
            pending = tc
            break
    if not pending:
        raise HTTPException(500, "could not find pending capture_payment tool call")

    decline_msg = ToolMessage(
        tool_call_id=pending["id"],
        content=json.dumps({
            "declined": True,
            "status": "user_cancelled",
            "reason": "User declined the payment via approval modal.",
        }),
        name=CAPTURE_NODE,
    )
    # Inject the synthetic tool result as if the capture_payment node had
    # run · the graph advances to the model node, which reacts.
    await agent.aupdate_state(config, {"messages": [decline_msg]}, as_node=CAPTURE_NODE)
    return EventSourceResponse(stream_agent_events(
        agent_input=None,
        session_id=req.session_id,
    ))
