"""Wraps a2a-sdk's Client into one async function: brief in, structured dict out.

Why this shape:
  - The Planner thinks in LangChain @tool functions. A tool returns a str
    (or JSON-stringified dict) after one await. Wrapping the A2A task
    lifecycle behind one function call keeps Planner code simple.
  - The sub-agent's INTERNAL events stay inside that sub-agent's trace ·
    the Planner sees one tool started + one tool finished. That's the
    whole point of the supervisor pattern: each layer hides its own
    detail from the layer above.

Phase 3.5 shape (this revision):
  - Returns a Python dict, not a plain string:
        {
          "text": "I recommend Japan Airlines JL754 ...",     # prose summary
          "artifacts": [                                       # structured data
            {"name": "search_flights", "artifact_id": "art-1-search_flights",
             "text": "", "data": {"result": [{flight_id, ...}, ...]}},
            ...
          ]
        }
  - Caller (planner @tool) `json.dumps`-es the dict before returning it to
    the LangGraph ToolNode. The LLM sees JSON in the tool result · its
    prompt tells it to PARAPHRASE the `text` field and IGNORE the
    `artifacts` data (which is for the UI cards, not the chat).
  - Frontend extracts the artifacts from the tool.finished SSE event
    payload and dispatches the same store actions it used to fire on
    direct search_flights / search_hotels tools.

A2A spec §3.7 mandates that structured task outputs go in Artifacts
rather than Messages · DataPart-in-Message is for interaction payloads
(forms, auth challenges) not for results.

Protobuf gotcha:
  - `Part.data` is `google.protobuf.Value`; numbers there are doubles.
    Integers like `pax`, `price_total_inr`, `stops` round-trip as floats
    (`50000` → `50000.0`). TypeScript/JS treats them identically (Number
    is always double precision) so the frontend isn't affected. If a
    Python consumer needs ints, cast explicitly: `int(d["price"])`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from a2a.client import Client, ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)

log = logging.getLogger(__name__)

# A2A sub-agents can take 15-30s to respond (their inner LangGraph runs
# an LLM call + MCP round-trip + possibly more LLM calls). Default httpx
# 5s timeout is way too short.
_HTTPX_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=10.0)
_httpx_client: httpx.AsyncClient | None = None


def _get_httpx_client() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(timeout=_HTTPX_TIMEOUT)
    return _httpx_client


# One Client per agent URL.
_clients: dict[str, Client] = {}


async def _get_client(agent_url: str) -> Client:
    # create_client is async in a2a-sdk v1.x · the signature annotation
    # claims it returns Client but it actually returns Awaitable[Client]
    # (it resolves the agent card before constructing the transport).
    if agent_url not in _clients:
        log.info("a2a.client.create url=%s timeout_read=%ss",
                 agent_url, _HTTPX_TIMEOUT.read)
        config = ClientConfig(httpx_client=_get_httpx_client())
        _clients[agent_url] = await create_client(agent_url, client_config=config)
    return _clients[agent_url]


def _text_from_message(msg: Message | None) -> str:
    """Concatenate every text-part of a Message into one string."""
    if msg is None:
        return ""
    out: list[str] = []
    for part in msg.parts:
        if part.WhichOneof("content") == "text":
            out.append(part.text)
    return "".join(out)


def _artifact_to_dict(artifact: Any) -> dict[str, Any]:
    """Convert a protobuf Artifact to a JSON-serialisable dict.

    `get_data_parts` returns `list[Any]` where each entry is the
    MessageToDict of one DataPart's Value. We always emit one DataPart
    per artifact on the server side, so `data_parts[0]` is the payload
    dict; the list is preserved as `data_parts` for completeness.

    `get_text_parts` returns `list[str]`. Joined with newlines for the
    `text` convenience field.
    """
    text_parts = get_text_parts(artifact.parts)
    data_parts = get_data_parts(artifact.parts)
    return {
        "name": artifact.name,
        "artifact_id": artifact.artifact_id,
        "text": "\n".join(text_parts) if text_parts else "",
        "data": data_parts[0] if data_parts else None,
        "data_parts": list(data_parts),
    }


async def delegate_to_a2a_agent(agent_url: str, brief: str) -> dict[str, Any]:
    """Send `brief` to the A2A sub-agent at `agent_url` and return its reply.

    Returns:
        {
          "text":      str        · sub-agent's final natural-language reply
          "artifacts": list[dict] · structured outputs · each dict has
                                    {name, artifact_id, text, data, data_parts}
        }

    Raises RuntimeError on TASK_STATE_FAILED with the agent's error
    message if it provided one.

    The Client streams a sequence of StreamResponse events. In
    non-streaming mode the SDK delivers a single StreamResponse whose
    `task` field contains the final Task (with all artifacts accumulated
    in `task.artifacts`). In streaming mode we also see intermediate
    `status_update` and `artifact_update` events.
    """
    client = await _get_client(agent_url)

    request = SendMessageRequest(
        message=Message(
            message_id=f"delegate-{uuid.uuid4().hex[:12]}",
            role=Role.ROLE_USER,
            parts=[Part(text=brief)],
        ),
    )

    final_text = ""
    error_text: str | None = None
    artifacts_out: list[dict[str, Any]] = []
    seen_artifact_ids: set[str] = set()

    def _add_artifact(art: Any) -> None:
        if art.artifact_id in seen_artifact_ids:
            return
        seen_artifact_ids.add(art.artifact_id)
        artifacts_out.append(_artifact_to_dict(art))

    async for event in client.send_message(request):
        if event.HasField("task"):
            task = event.task
            if task.status.state == TaskState.TASK_STATE_COMPLETED:
                final_text = _text_from_message(task.status.message) or final_text
            elif task.status.state == TaskState.TASK_STATE_FAILED:
                error_text = _text_from_message(task.status.message) or "task failed"
            for art in task.artifacts:
                _add_artifact(art)
        elif event.HasField("status_update"):
            su = event.status_update
            if su.status.state == TaskState.TASK_STATE_COMPLETED:
                final_text = _text_from_message(su.status.message) or final_text
            elif su.status.state == TaskState.TASK_STATE_FAILED:
                error_text = _text_from_message(su.status.message) or "task failed"
        elif event.HasField("artifact_update"):
            _add_artifact(event.artifact_update.artifact)
        elif event.HasField("message"):
            if not final_text:
                final_text = _text_from_message(event.message)

    if error_text and not final_text:
        raise RuntimeError(f"a2a sub-agent failed: {error_text}")

    log.info(
        "a2a.delegate.done url=%s artifacts=%d text_len=%d",
        agent_url, len(artifacts_out), len(final_text),
    )

    return {
        "text": final_text or "(no response from sub-agent)",
        "artifacts": artifacts_out,
    }
