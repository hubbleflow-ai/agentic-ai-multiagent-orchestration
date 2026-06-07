"""planner · SSE event producer shared by /agent/stream, /agent/resume, /agent/cancel.

Spec of events emitted on the SSE stream:

    state.phase              · {phase: "thinking" | "tool_calling" | "responding" |
                                       "done" | "awaiting_approval"}
    tool.started             · {name, args}                    · outer @tool only
    tool.finished            · {name, result}                  · outer @tool only
    agent.message_segment    · {segment_id, content, reasoning}
    agent.audio_chunk        · {segment_id, data}              · 24kHz PCM b64
    agent.audio_done         · {segment_id, cached?, error?}
    agent.interrupt          · {kind, auth_id, amount_inr}     · Phase 7 HITL
    error                    · {message}
    run.complete             · {interrupted: bool}

Concurrency model:
  - One producer task runs astream_events from the LangGraph agent.
  - Each model segment kicks off a parallel TTS task that pushes audio
    chunks into the same queue.
  - A drain loop yields queue items as SSE dicts back to the client.
  - Sentinel `None` on the queue stops the drain.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from services.planner.graph import CAPTURE_NODE, agent, extract_capture_intent, split_content
from services.planner.tools import PLANNER_TOOL_NAMES, _trip_id
from services.planner.tts import stream_tts_to_queue

log = logging.getLogger(__name__)


async def stream_agent_events(
    agent_input: dict | None,
    session_id: str,
) -> AsyncIterator[dict]:
    """SSE generator · used for fresh turns and for HITL resume / cancel.

    Pass `agent_input={"messages": [HumanMessage(...)]}` for a fresh turn.
    Pass `agent_input=None` to resume the graph from its checkpointer (after
    the HITL gate is approved or after a synthetic decline is injected).
    """
    config = {"configurable": {"thread_id": session_id}}

    # ContextVar bound INSIDE the generator · sse_starlette runs this in
    # its own asyncio TaskGroup, a different context than the request
    # handler, so binding outside would make reset() raise ValueError.
    token = _trip_id.set(session_id)

    queue: asyncio.Queue = asyncio.Queue()
    tts_tasks: list[asyncio.Task] = []
    segment_counter = 0
    # Run-id ledger · suppresses tool.started/end events for nested MCP
    # tools fired from inside outer @tool wrappers (see filter below).
    active_outer_runs: set[str] = set()

    async def producer() -> None:
        nonlocal segment_counter
        try:
            await queue.put({
                "event": "state.phase",
                "data": json.dumps({"phase": "thinking"}),
            })

            async for ev in agent.astream_events(agent_input, config=config, version="v2"):
                kind = ev.get("event")

                if kind == "on_chat_model_end":
                    output = ev["data"].get("output")
                    text, reasoning = split_content(
                        getattr(output, "content", None) if output else None
                    )
                    if text or reasoning:
                        segment_counter += 1
                        segment_id = f"seg-{segment_counter}"
                        await queue.put({
                            "event": "agent.message_segment",
                            "data": json.dumps({
                                "segment_id": segment_id,
                                "content": text or "",
                                "reasoning": reasoning or "",
                            }),
                        })
                        # Kick off TTS in parallel · audio chunks for this
                        # segment will arrive on the same SSE stream.
                        if text and text.strip():
                            tts_tasks.append(asyncio.create_task(
                                stream_tts_to_queue(text, segment_id, queue)
                            ))

                elif kind == "on_tool_start":
                    # Suppress nested MCP tool events fired from inside our
                    # @tool wrappers. Two filters in order:
                    #
                    # (a) Name check · drop events for tools NOT in the
                    #     planner's TOOLS set (e.g. inner MCP 'authorize'
                    #     called from outer @tool 'authorize_payment').
                    #
                    # (b) Lineage check · for tools whose name COLLIDES
                    #     between outer @tool and inner MCP (set_budget,
                    #     commit_spend, check_budget, add_events ·
                    #     same name on both sides), the inner call's event
                    #     has the OUTER call's run_id in its parent_ids.
                    #     Skip if so.
                    if ev["name"] not in PLANNER_TOOL_NAMES:
                        continue
                    parent_ids = ev.get("parent_ids", []) or []
                    if any(p in active_outer_runs for p in parent_ids):
                        continue
                    run_id = ev.get("run_id")
                    if run_id:
                        active_outer_runs.add(run_id)
                    args = ev["data"].get("input", {}) or {}
                    if isinstance(args, dict) and set(args.keys()) == {"input"}:
                        args = args["input"]
                    await queue.put({
                        "event": "state.phase",
                        "data": json.dumps({"phase": "tool_calling"}),
                    })
                    await queue.put({
                        "event": "tool.started",
                        "data": json.dumps({
                            "name": ev["name"],
                            "args": args if isinstance(args, dict) else {"input": str(args)},
                        }),
                    })

                elif kind == "on_tool_end":
                    # Mirror the on_tool_start filter: only emit if the
                    # run_id is tracked as an outer @tool.
                    if ev["name"] not in PLANNER_TOOL_NAMES:
                        continue
                    run_id = ev.get("run_id")
                    if run_id not in active_outer_runs:
                        continue
                    active_outer_runs.discard(run_id)
                    out = ev["data"].get("output", "")
                    result_text = getattr(out, "content", out)
                    try:
                        result = json.loads(result_text) if isinstance(result_text, str) else result_text
                    except (ValueError, TypeError):
                        result = result_text
                    await queue.put({
                        "event": "tool.finished",
                        "data": json.dumps({"name": ev["name"], "result": result}),
                    })
                    await queue.put({
                        "event": "state.phase",
                        "data": json.dumps({"phase": "thinking"}),
                    })

            # Wait for all TTS tasks to complete before signaling end so
            # the user hears every narration even if the planner finishes
            # before some TTS chunks have streamed.
            if tts_tasks:
                await asyncio.gather(*tts_tasks, return_exceptions=True)

            # Phase 7 · check if the graph paused at the HITL interrupt
            # (capture_payment). If so, emit agent.interrupt so the
            # frontend can render the approval modal. Run does NOT mark
            # 'done' in that case · it ends in 'awaiting_approval' until
            # the user clicks Approve (→ /agent/resume) or Cancel
            # (→ /agent/cancel).
            interrupted = False
            try:
                paused_state = await agent.aget_state(config)
                next_nodes = paused_state.next or ()
                if CAPTURE_NODE in next_nodes:
                    auth_id, amount_inr = extract_capture_intent(paused_state)
                    await queue.put({
                        "event": "agent.interrupt",
                        "data": json.dumps({
                            "kind": "payment_approval",
                            "auth_id": auth_id,
                            "amount_inr": amount_inr,
                        }),
                    })
                    await queue.put({
                        "event": "state.phase",
                        "data": json.dumps({"phase": "awaiting_approval"}),
                    })
                    interrupted = True
            except Exception:
                log.exception("planner.interrupt_check_failed session=%s", session_id)

            if not interrupted:
                await queue.put({
                    "event": "state.phase",
                    "data": json.dumps({"phase": "done"}),
                })
            await queue.put({
                "event": "run.complete",
                "data": json.dumps({"interrupted": interrupted}),
            })
        except Exception as e:
            log.exception("planner.agent.producer_error session=%s", session_id)
            await queue.put({
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            })
        finally:
            await queue.put(None)   # sentinel · drain loop exits

    producer_task = asyncio.create_task(producer())

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    except Exception:
        log.exception("planner.agent.drain_error session=%s", session_id)
    finally:
        try:
            await producer_task
        except Exception:
            pass
        try:
            _trip_id.reset(token)
        except ValueError:
            pass
