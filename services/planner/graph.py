"""planner · LangGraph supervisor + Phase 7 HITL gate.

Graph shape:

      START ─▶ model ─▶ (route_after_model)
                          │
                          ├─▶ regular_tools ──┐
                          ├─▶ capture_payment ┤   (interrupt_before=["capture_payment"])
                          └─▶ END             │
                          ▲                   │
                          └───────────────────┘

`call_model` invokes the Gemini LLM with the SYSTEM_PROMPT prepended.

`route_after_model` is a custom conditional · if the model emits a
`capture_payment` tool call we route to the SEPARATE node so the
LangGraph compiler's `interrupt_before` pauses BEFORE the actual capture
runs (Phase 7 HITL gate). Otherwise we route to the regular tool node.

`extract_capture_intent(state)` mines the paused graph state for the
auth_id (from the pending tool call) + amount_inr (from the prior
authorize_payment ToolMessage). The SSE producer emits these in the
`agent.interrupt` event so the frontend modal can show the amount.

`split_content(content)` is a helper that splits a LangChain message
content into (visible_text, reasoning) handling the various shapes
Gemini emits when include_thoughts=True.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from services.planner.config import GEMINI_MODEL, GEMINI_THINKING_LEGACY, GEMINI_THINKING_LEVEL
from services.planner.dynamic_a2a_tools import build_all_a2a_tools
from services.planner.prompt import SYSTEM_PROMPT
from services.planner.tools import PLANNER_TOOL_NAMES, STATIC_TOOLS

# ─── tool catalogue · 6 static MCP-backed + 6 dynamic A2A = 12 total ─────
# All 6 delegate_to_<agent> tools are BUILT at startup from each sub-
# agent's Agent Card. See dynamic_a2a_tools.py. The planner has ZERO
# hand-written knowledge of any sub-agent's capabilities · everything
# comes from cards. Docker `depends_on: service_healthy` on every sub-
# agent guarantees the cards are reachable by the time this import runs.

_a2a_tools = build_all_a2a_tools()
TOOLS = STATIC_TOOLS + _a2a_tools

# Keep the SSE producer's outer-tool filter in sync with what we actually
# expose to the LLM · without this the dynamic A2A tools' events would
# be suppressed (their names aren't in PLANNER_TOOL_NAMES at import time
# of tools.py · they're only known after build_all_a2a_tools runs).
for _t in _a2a_tools:
    PLANNER_TOOL_NAMES.add(_t.name)

log = logging.getLogger(__name__)


# ─── LLM setup ───────────────────────────────────────────────────────────
# Match the Session 5 ReasoningStrategy config:
#   - gemini-3.5-flash (better reasoning than 2.5-flash)
#   - include_thoughts=True so the model emits reasoning we can surface
#   - disable_streaming=True because Gemini's streaming path SILENTLY DROPS
#     `thought` parts when the same response contains a `function_call`.
#     We're a tool-calling agent → this bug would hit us on every turn.
#     ainvoke (non-streaming) preserves both reasoning AND tool calls.
#   - thinking_level controls effort: minimal | low | medium | high
#
# Trade-off vs streaming: chunks don't fire token-by-token. The SSE
# event handler catches the full message via on_chat_model_end instead.

_llm_kwargs: dict[str, Any] = {
    "model": GEMINI_MODEL,
    "temperature": 0,
    "include_thoughts": True,
    "disable_streaming": True,
}
if GEMINI_THINKING_LEGACY:
    # 2.5-family models use the older numeric thinking_budget API.
    # -1 = let the model decide.
    _llm_kwargs["thinking_budget"] = -1
else:
    # 3.x-family uses the enum thinking_level (default: medium).
    _llm_kwargs["thinking_level"] = GEMINI_THINKING_LEVEL

_llm = ChatGoogleGenerativeAI(**_llm_kwargs)
_llm_with_tools = _llm.bind_tools(TOOLS)


async def call_model(state: MessagesState) -> dict:
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = await _llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


# ─── Phase 7 · split capture_payment into its own node for HITL gate ─────

CAPTURE_NODE = "capture_payment"
REGULAR_NODE = "regular_tools"

_regular_tool_node = ToolNode([t for t in TOOLS if t.name != CAPTURE_NODE])
_capture_tool_node = ToolNode([t for t in TOOLS if t.name == CAPTURE_NODE])


def _route_after_model(state: MessagesState) -> str:
    """Route tool calls · capture goes to its own (interrupted) node."""
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return END
    if any(tc.get("name") == CAPTURE_NODE for tc in tool_calls):
        return CAPTURE_NODE
    return REGULAR_NODE


# ─── compile ─────────────────────────────────────────────────────────────

_graph = StateGraph(MessagesState)
_graph.add_node("model", call_model)
_graph.add_node(REGULAR_NODE, _regular_tool_node)
_graph.add_node(CAPTURE_NODE, _capture_tool_node)
_graph.add_edge(START, "model")
_graph.add_conditional_edges("model", _route_after_model, {
    REGULAR_NODE: REGULAR_NODE,
    CAPTURE_NODE: CAPTURE_NODE,
    END: END,
})
_graph.add_edge(REGULAR_NODE, "model")
_graph.add_edge(CAPTURE_NODE, "model")

checkpointer = MemorySaver()
agent = _graph.compile(
    checkpointer=checkpointer,
    # HITL gate · graph pauses BEFORE capture_payment executes so the
    # frontend can show an approval modal. Resuming via /agent/resume
    # fires the actual capture (the protocol enforces the pause; the
    # LLM can't bypass it).
    interrupt_before=[CAPTURE_NODE],
)


# ─── helpers used by the SSE producer ────────────────────────────────────

def extract_capture_intent(state) -> tuple[str | None, int | None]:
    """Pull the pending capture's auth_id + amount from the paused graph state.

    auth_id    · from the model's pending capture_payment tool_call.args
    amount_inr · from the most recent authorize_payment ToolMessage content
                 (we don't have it on the capture call itself · the model
                 only passes auth_id to capture).
    """
    messages = state.values.get("messages", []) if state.values else []

    auth_id: str | None = None
    for m in reversed(messages):
        if getattr(m, "type", "") != "ai":
            continue
        for tc in getattr(m, "tool_calls", None) or []:
            if tc.get("name") == CAPTURE_NODE:
                auth_id = (tc.get("args") or {}).get("auth_id")
                break
        if auth_id is not None:
            break

    amount_inr: int | None = None
    for m in reversed(messages):
        if getattr(m, "type", "") != "tool":
            continue
        if getattr(m, "name", "") != "authorize_payment":
            continue
        content = getattr(m, "content", "")
        try:
            data = json.loads(content) if isinstance(content, str) else content
            if isinstance(data, dict):
                amt = data.get("amount_inr")
                if amt is not None:
                    amount_inr = int(amt)
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        break

    return auth_id, amount_inr


def split_content(content: Any) -> tuple[str, str]:
    """Split a LangChain message's `content` into (visible_text, reasoning).

    Gemini with `include_thoughts=True` returns thoughts in two possible
    shapes depending on langchain-google-genai version:

      Shape A (older · type-tagged):
        [{"type": "text", "text": "..."},
         {"type": "thinking", "text": "..."}]

      Shape B (newer · raw Gemini parts via LangChain):
        [{"type": "text", "text": "..."},
         {"type": "text", "text": "...", "thought": true}]

    Plus the message might also be a plain string when no thoughts emitted.
    Handle all three so reasoning surfaces regardless of SDK version.
    """
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content, ""
    if isinstance(content, list):
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                continue

            # Shape A · type field signals thinking
            btype = block.get("type", "")
            # Shape B · boolean flag on a regular text block
            is_thought_flag = bool(block.get("thought") is True)

            text_val = (
                block.get("text")
                or block.get("thinking")
                or (block.get("thought") if isinstance(block.get("thought"), str) else None)
                or ""
            )
            if not isinstance(text_val, str) or not text_val:
                continue

            if is_thought_flag or btype in ("thinking", "thought", "reasoning"):
                reasoning_parts.append(text_val)
            else:
                text_parts.append(text_val)
        return "".join(text_parts), "".join(reasoning_parts)
    return str(content), ""
