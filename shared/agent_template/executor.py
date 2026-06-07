"""LangGraph → A2A AgentExecutor bridge.

The contract A2A's `DefaultRequestHandler` needs is `AgentExecutor`, an
abstract class with `execute(context, event_queue)` and `cancel(...)`.
Our sub-agents are compiled LangGraph state graphs. This module bridges
the two so every sub-agent in Phase 4 reuses the same bridge code.

Phase 3.5 scope (this revision):
  - Invoke the graph to completion.
  - Walk the resulting MessagesState; for each `ToolMessage` whose content
    parses as JSON, emit a `TaskArtifactUpdateEvent` carrying a
    `Part(data=...)` · this is the A2A-spec-correct way for a sub-agent
    to return structured outputs to its caller (spec §3.7: "Messages
    SHOULD NOT be used to deliver task outputs. Results SHOULD BE
    returned using Artifacts associated with a Task").
  - Emit the final assistant text via TaskUpdater.complete(message=...).

The supervisor-side `delegate_to_a2a_agent` helper in
`shared/a2a_helpers/client.py` reads `task.artifacts`, unmarshalls each
DataPart back to a Python dict via `get_data_parts`, and returns the
combined `{text, artifacts}` envelope so the Planner can both speak the
prose summary AND surface the structured data to the frontend cards.

Implementation notes:
  - `Part.data` is `google.protobuf.Value`. Numbers round-trip as doubles
    (so ints become floats on receive) · documented in the helper.
  - We strip the MultiServerMCPClient `<server>__<tool>` prefix from
    artifact names so the supervisor sees the logical tool name
    (`search_flights` not `airline__search_flights`).
  - Tool results that don't JSON-parse are skipped silently · they
    might be plain-text outputs that have no structured shape.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from a2a.helpers import new_data_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

log = logging.getLogger(__name__)

GraphBuilder = Callable[[], Awaitable[Any]]


class LangGraphA2AExecutor(AgentExecutor):
    """Wrap a compiled LangGraph agent so it can be served via the A2A SDK.

    Subclasses provide a `graph_builder` async callable that returns a
    compiled LangGraph. The builder runs lazily on first request · this
    matters because loading MCP tools is async and cannot happen at
    module import time.
    """

    def __init__(
        self,
        graph_builder: GraphBuilder,
        *,
        agent_name: str = "agent",
    ) -> None:
        self._graph_builder = graph_builder
        self._agent_name = agent_name
        self._graph: Any = None

    async def _ensure_graph(self) -> Any:
        if self._graph is None:
            log.info("%s · building graph on first request", self._agent_name)
            self._graph = await self._graph_builder()
        return self._graph

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        graph = await self._ensure_graph()
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # On a brand-new task, we must FIRST enqueue the Task object itself
        # (with state=SUBMITTED) before any TaskStatusUpdateEvent · the
        # consumer side of the A2A pipe rejects status updates that have no
        # preceding Task to attach to ('Agent should enqueue Task before
        # TaskStatusUpdateEvent'). On continuation messages the task already
        # exists, so we skip straight to a WORKING status update.
        if context.current_task is None:
            initial_task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
            await event_queue.enqueue_event(initial_task)
        await updater.start_work()

        user_input = context.get_user_input() or ""
        log.info("%s.execute · input=%r", self._agent_name, user_input[:120])

        try:
            result = await graph.ainvoke(
                {"messages": [{"role": "user", "content": user_input}]},
            )
            messages = result.get("messages", [])
            final_text = _final_ai_text(messages) or "(no response)"
        except Exception as e:
            log.exception("%s.execute · graph invocation failed", self._agent_name)
            error_msg = updater.new_agent_message(
                parts=[Part(text=f"{self._agent_name} error: {e}")]
            )
            await updater.failed(error_msg)
            return

        # Spec §3.7 · emit each successful tool result as an Artifact
        # carrying a DataPart. The Planner's a2a-helpers client extracts
        # these via get_data_parts() and forwards to the frontend.
        await self._emit_tool_artifacts(updater, messages)

        log.info("%s.execute · final=%r", self._agent_name, final_text[:120])
        final_msg = updater.new_agent_message(parts=[Part(text=final_text)])
        await updater.complete(final_msg)

    async def _emit_tool_artifacts(
        self,
        updater: TaskUpdater,
        messages: list,
    ) -> None:
        """For each ToolMessage with JSON content, emit an A2A Artifact.

        The artifact's `name` is the logical tool name (server prefix
        stripped for MCP tools loaded via MultiServerMCPClient, which
        otherwise namespaces tools as '<server>__<tool>').

        Non-dict JSON payloads (e.g. a raw list of options) get wrapped
        in `{"result": <list>}` so the DataPart's protobuf Value can
        always parse from a JSON-object top level.
        """
        counter = 0
        for m in messages:
            if not _is_tool_message(m):
                continue
            tool_name = _logical_tool_name(getattr(m, "name", None) or "tool")
            parsed = _try_parse_json(getattr(m, "content", None))
            if parsed is None:
                continue
            envelope = parsed if isinstance(parsed, dict) else {"result": parsed}
            counter += 1
            try:
                await updater.add_artifact(
                    parts=[new_data_part(envelope, media_type="application/json")],
                    artifact_id=f"art-{counter}-{tool_name}",
                    name=tool_name,
                    last_chunk=True,
                )
                log.info(
                    "%s.artifact name=%s keys=%s",
                    self._agent_name,
                    tool_name,
                    list(envelope.keys()) if isinstance(envelope, dict) else "scalar",
                )
            except Exception as e:
                log.warning(
                    "%s.artifact.emit_failed name=%s err=%s",
                    self._agent_name, tool_name, e,
                )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Phase 2 minimal · streaming cancellation lands with Phase 8 polish.
        raise NotImplementedError(f"{self._agent_name} does not support cancel yet")


# ─── helpers ─────────────────────────────────────────────────────────────

def _is_tool_message(m: Any) -> bool:
    """Detect LangChain ToolMessage without importing langchain here.

    Avoids creating a hard dep at module load time; the LangGraph sub-agent
    obviously has langchain installed but the shared template should not
    require it just to be importable.
    """
    if getattr(m, "type", "") == "tool":
        return True
    return type(m).__name__ == "ToolMessage"


def _logical_tool_name(raw_name: str) -> str:
    """Strip MultiServerMCPClient's '<server>__<tool>' prefix.

    Example: 'airline__search_flights' → 'search_flights'.
    Leaves non-prefixed names alone.
    """
    if "__" in raw_name:
        return raw_name.split("__", 1)[1]
    return raw_name


def _try_parse_json(content: Any) -> Any:
    """Parse `content` as JSON if possible, then unwrap MCP content blocks.

    LangChain MCP adapter returns ToolMessage content as a JSON-stringified
    list of MCP TextContent blocks: `[{"type":"text","text":"<JSON>","id":"..."},
    ...]` · one block per item in the underlying tool's return list. We
    detect this shape and unwrap: each block's `text` is the JSON-serialised
    actual payload (e.g. one FlightOption dict). The unwrapped list is what
    downstream consumers actually want.

    Returns the parsed (and unwrapped) value or None if not JSON-parseable.
    """
    if content is None:
        return None
    parsed: Any
    if isinstance(content, (dict, list)):
        parsed = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
    else:
        return None
    return _unwrap_mcp_content_blocks(parsed)


def _unwrap_mcp_content_blocks(parsed: Any) -> Any:
    """Unwrap MCP `[{type:'text', text:'<json>'}, ...]` blocks to a flat list.

    Only triggers when EVERY element is a dict with `type='text'` and a
    `text` string · otherwise returns `parsed` unchanged. If a block's
    `text` doesn't itself JSON-parse, falls back to the raw string for
    that block so we never silently lose data.
    """
    if not isinstance(parsed, list) or not parsed:
        return parsed
    if not all(
        isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        for b in parsed
    ):
        return parsed
    unwrapped: list[Any] = []
    for block in parsed:
        raw = block["text"]
        try:
            unwrapped.append(json.loads(raw))
        except (ValueError, TypeError):
            unwrapped.append(raw)
    # MCP wraps single-value returns (one dict / one list) as a single
    # TextContent block. Flatten len==1 so downstream consumers see the
    # actual value, not a 1-element list wrapping it. For multi-item
    # returns (e.g. search_flights' 12 options · each becomes one block)
    # we keep the list intact.
    if len(unwrapped) == 1:
        return unwrapped[0]
    return unwrapped


def _final_ai_text(messages: list) -> str:
    """Pull the last AIMessage's text content out of a MessagesState result.

    LangChain message `content` is either a plain string OR a list of
    content blocks (Gemini with `include_thoughts=True` returns blocks).
    Handle both.
    """
    for m in reversed(messages):
        if getattr(m, "type", "") != "ai":
            continue
        content = getattr(m, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    # Skip thought blocks · they are model-internal reasoning,
                    # not the user-facing answer.
                    if block.get("type") == "thinking":
                        continue
                    text = block.get("text")
                    if text:
                        parts.append(str(text))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content)
    return ""
