/**
 * Glue between the agent SSE stream and Redux · segment-per-invocation model.
 *
 *   (caller dispatches runAgent)
 *   → push user turn
 *   → reset agentSlice for this run
 *   → POST /agent/stream and consume SSE events
 *
 *   tool.started               · agentSlice.toolStarted (live)
 *   tool.finished              · agentSlice.toolFinished + tripSlice update
 *   agent.message_segment      · attach live tools to PREVIOUS assistant
 *                                  turn (the one that called them), then
 *                                  push a NEW assistant turn with this
 *                                  segment's content + reasoning, then
 *                                  clear the live toolChain
 *   error                      · setError + setLatestAssistantError
 *   run.complete               · attach remaining live tools to the LATEST
 *                                  assistant turn, clear live state
 */

import type { AppDispatch, RootState } from ".";
import { streamAgent, resumeAgent, cancelAgent, type AgentEvent } from "@/lib/api";
import {
  pushUserTurn,
  pushAssistantTurn,
  attachToolsToLatestAssistant,
  setLatestAssistantError,
} from "./chatSlice";
import { notifyPanelUpdate, resetPanelForNewRun } from "./uiSlice";
import {
  clearLiveTools,
  resetForNewRun,
  setError,
  setPhase,
  toolFinished,
  toolStarted,
  setPendingApproval,
  setApprovalStatus,
} from "./agentSlice";
import {
  startFlightSearch,
  finishFlightSearch,
  holdFlightById,
  startHotelSearch,
  finishHotelSearch,
  holdHotelById,
  upsertItinerary,
  setBudget,
  setPaymentAuth,
  setPaymentTransaction,
  setCalendar,
  setTodos,
  flightSegmentKey,
  hotelStayKey,
  type FlightOption,
  type HotelOption,
} from "./tripSlice";
import {
  setSpeaking,
  setSpokenMessageId,
} from "./voiceSlice";
import {
  playAudioChunk,
  finalizeAudioSegment,
} from "@/lib/voice";

function applyToolStartedToTrip(
  dispatch: AppDispatch,
  toolName: string,
  args: Record<string, unknown>,
): boolean {
  if (toolName === "search_flights") {
    const origin = String(args.origin ?? "");
    const destination = String(args.destination ?? "");
    const depart_date = String(args.depart_date ?? "");
    const return_date = args.return_date ? String(args.return_date) : null;
    if (!origin || !destination || !depart_date) return false;
    dispatch(startFlightSearch({
      key: flightSegmentKey(origin, destination, depart_date),
      origin,
      destination,
      depart_date,
      return_date,
    }));
    return true;
  }
  if (toolName === "search_hotels") {
    const city = String(args.city ?? "");
    const check_in = String(args.check_in ?? "");
    const check_out = String(args.check_out ?? "");
    if (!city || !check_in || !check_out) return false;
    dispatch(startHotelSearch({
      key: hotelStayKey(city, check_in, check_out),
      city,
      check_in,
      check_out,
    }));
    return true;
  }
  return false;
}

function applyToolResultToTrip(
  dispatch: AppDispatch,
  toolName: string,
  args: Record<string, unknown>,
  result: unknown,
): boolean {
  if (!result || typeof result !== "object") return false;
  const r = result as Record<string, any>;

  switch (toolName) {
    case "search_flights": {
      const origin = String(args.origin ?? r.options?.[0]?.origin ?? "");
      const destination = String(args.destination ?? r.options?.[0]?.destination ?? "");
      const depart_date = String(args.depart_date ?? r.options?.[0]?.depart_date ?? "");
      if (!origin || !destination || !depart_date) return false;
      dispatch(finishFlightSearch({
        key: flightSegmentKey(origin, destination, depart_date),
        options: (r.options ?? []) as FlightOption[],
        recommendedId: r.recommended_id ?? null,
      }));
      return true;
    }
    case "hold_flight": {
      if (r.flight_id && r.hold_id) {
        dispatch(holdFlightById({ flight_id: r.flight_id, hold_id: r.hold_id }));
      }
      return true;
    }
    case "delegate_to_flight_agent": {
      // Phase 3.5 · A2A artifacts. The sub-agent emits one Artifact per
      // tool call (search_flights / hold_flight) carrying a DataPart with
      // the structured payload. The Planner forwards the whole envelope
      // here as {text, artifacts: [{name, data}, ...]}. We pluck the
      // structured data and dispatch the SAME trip-store actions that
      // used to fire on direct search_flights / hold_flight tool events.
      //
      // Protobuf Value note: ints came through as floats (TS doesn't
      // distinguish, so the cards render fine).
      const artifacts = Array.isArray(r.artifacts) ? r.artifacts : [];
      let any = false;
      for (const art of artifacts) {
        const name = String(art?.name ?? "");
        const data = art?.data ?? null;
        if (!data || typeof data !== "object") continue;

        if (name === "search_flights") {
          // mcp-airline.search_flights returns a list of FlightOption; our
          // executor wraps top-level lists in {"result": [...]} so DataPart
          // always serialises from a JSON object. Frontend reads both shapes.
          const options = (data.result ?? data.options ?? []) as FlightOption[];
          if (!options.length) continue;
          const first = options[0];
          if (!first?.origin || !first?.destination || !first?.depart_date) continue;
          const key = flightSegmentKey(first.origin, first.destination, first.depart_date);
          dispatch(startFlightSearch({
            key,
            origin: first.origin,
            destination: first.destination,
            depart_date: first.depart_date,
            return_date: first.return_date ?? null,
          }));
          // Sub-agent's recommendation isn't surfaced through artifacts yet
          // (the prose summary in `text` is where it lives). Default to the
          // first option (cheapest, since mcp-airline sorts ascending).
          dispatch(finishFlightSearch({
            key,
            options,
            recommendedId: options[0].flight_id,
          }));
          any = true;
        }
        if (name === "hold_flight") {
          // MCP TextContent wraps each return value in a list-of-blocks ·
          // for single-value tools that yields `data.result` as a 1-element
          // array. Take [0] when array, fall back to object/data otherwise.
          const raw = data.result ?? data;
          const held = (Array.isArray(raw) ? raw[0] : raw) as
            | Record<string, unknown>
            | undefined;
          const flightId = held?.flight_id as string | undefined;
          const holdId = held?.hold_id as string | undefined;
          if (flightId && holdId) {
            dispatch(holdFlightById({ flight_id: flightId, hold_id: holdId }));
            any = true;
          }
        }
      }
      return any;
    }
    case "search_hotels": {
      const city = String(args.city ?? r.options?.[0]?.city ?? "");
      const check_in = String(args.check_in ?? "");
      const check_out = String(args.check_out ?? "");
      if (!city || !check_in || !check_out) return false;
      dispatch(finishHotelSearch({
        key: hotelStayKey(city, check_in, check_out),
        options: (r.options ?? []) as HotelOption[],
        recommendedId: r.recommended_id ?? null,
      }));
      return true;
    }
    case "hold_hotel": {
      if (r.hotel_id && r.hold_id) {
        dispatch(holdHotelById({ hotel_id: r.hotel_id, hold_id: r.hold_id }));
      }
      return true;
    }
    case "delegate_to_hotel_agent": {
      // Phase 4 · same artifact pattern as delegate_to_flight_agent.
      // Sub-agent emits one Artifact per MCP tool call (search_hotels /
      // hold_hotel) carrying a DataPart with the structured payload.
      const artifacts = Array.isArray(r.artifacts) ? r.artifacts : [];
      let any = false;
      for (const art of artifacts) {
        const name = String(art?.name ?? "");
        const data = art?.data ?? null;
        if (!data || typeof data !== "object") continue;

        if (name === "search_hotels") {
          const options = (data.result ?? data.options ?? []) as HotelOption[];
          if (!options.length) continue;
          const first = options[0] as any;
          const city = String(first?.city ?? "");
          // mcp-hotel stamps check_in / check_out on every option from the
          // call args so consumers can key by stay.
          const checkIn = String(first?.check_in ?? "");
          const checkOut = String(first?.check_out ?? "");
          if (!city || !checkIn || !checkOut) continue;
          const key = hotelStayKey(city, checkIn, checkOut);
          dispatch(startHotelSearch({
            key,
            city,
            check_in: checkIn,
            check_out: checkOut,
          }));
          dispatch(finishHotelSearch({
            key,
            options,
            recommendedId: options[0].hotel_id,
          }));
          any = true;
        }
        if (name === "hold_hotel") {
          const raw = data.result ?? data;
          const held = (Array.isArray(raw) ? raw[0] : raw) as
            | Record<string, unknown>
            | undefined;
          const hotelId = held?.hotel_id as string | undefined;
          const holdId = held?.hold_id as string | undefined;
          if (hotelId && holdId) {
            dispatch(holdHotelById({ hotel_id: hotelId, hold_id: holdId }));
            any = true;
          }
        }
      }
      return any;
    }
    case "build_itinerary":
    case "revise_itinerary": {
      if (Array.isArray(r.days) && r.city) {
        dispatch(upsertItinerary({ city: r.city, days: r.days }));
        return true;
      }
      return false;
    }
    case "delegate_to_itinerary_agent": {
      // Phase 4 closer · itinerary-agent emits build_itinerary or
      // revise_itinerary artifacts via mcp-trip-state. Both carry the
      // same {city, days[]} shape.
      const artifacts = Array.isArray(r.artifacts) ? r.artifacts : [];
      let any = false;
      for (const art of artifacts) {
        const name = String(art?.name ?? "");
        const data = art?.data ?? null;
        if (!data || typeof data !== "object") continue;
        if (name !== "build_itinerary" && name !== "revise_itinerary") continue;
        // mcp-trip-state returns a Pydantic model; executor wraps non-dict
        // shapes in {result:...} but Pydantic dicts pass through.
        const payload = (data.result ?? data) as Record<string, any>;
        const city = payload?.city as string | undefined;
        const days = payload?.days;
        if (city && Array.isArray(days)) {
          dispatch(upsertItinerary({ city, days }));
          any = true;
        }
      }
      return any;
    }
    case "delegate_to_todo_agent": {
      // Phase 4 closer · todo-agent emits create_todos artifact via
      // mcp-trip-state. Shape: {count, todos: [{id, text, priority, due_date}]}.
      const artifacts = Array.isArray(r.artifacts) ? r.artifacts : [];
      let any = false;
      for (const art of artifacts) {
        const name = String(art?.name ?? "");
        const data = art?.data ?? null;
        if (!data || typeof data !== "object") continue;
        if (name !== "create_todos") continue;
        const payload = (data.result ?? data) as Record<string, any>;
        const todos = payload?.todos;
        const count = payload?.count;
        if (Array.isArray(todos)) {
          dispatch(setTodos({ count: Number(count ?? todos.length), items: todos }));
          any = true;
        }
      }
      return any;
    }
    case "set_budget":
    case "commit_spend":
    case "check_budget": {
      const limit = r.limit_inr ?? 0;
      const spent = r.total_spent_inr ?? r.spent_inr ?? 0;
      const cats = r.categories ?? {};
      if (limit > 0 || spent > 0 || Object.keys(cats).length > 0) {
        dispatch(setBudget({ limit_inr: limit, spent_inr: spent, categories: cats }));
        return true;
      }
      return false;
    }
    case "authorize_payment": {
      if (r.auth_id) {
        dispatch(setPaymentAuth({ auth_id: r.auth_id, amount_inr: r.amount_inr ?? 0 }));
        return true;
      }
      return false;
    }
    case "capture_payment": {
      if (r.transaction_id) {
        dispatch(setPaymentTransaction({
          transaction_id: r.transaction_id,
          status: r.status ?? "approved",
        }));
        return true;
      }
      return false;
    }
    case "add_calendar_events": {
      dispatch(setCalendar({ count: r.created_count ?? 0, mode: r.mode ?? "dry-run" }));
      return true;
    }
    case "create_pretrip_todos": {
      dispatch(setTodos({ count: r.count ?? 0, items: r.todos ?? [] }));
      return true;
    }
  }
  return false;
}

export function runAgent(message: string) {
  return async (dispatch: AppDispatch, getState: () => RootState) => {
    const sessionId = getState().chat.sessionId;

    dispatch(pushUserTurn({ content: message }));
    dispatch(resetForNewRun());
    dispatch(resetPanelForNewRun());
    dispatch(setPhase("thinking"));

    try {
      await streamAgent(sessionId, message, (ev: AgentEvent) => {
        switch (ev.event) {
          case "state.phase":
            dispatch(setPhase(ev.data.phase));
            break;

          case "tool.started": {
            const args = ev.data.args ?? {};
            dispatch(toolStarted({ name: ev.data.name, args }));
            if (applyToolStartedToTrip(dispatch, ev.data.name, args)) {
              dispatch(notifyPanelUpdate());
            }
            break;
          }

          case "tool.finished": {
            const beforeChain = getState().agent.toolChain;
            const lastRunning = [...beforeChain]
              .reverse()
              .find((t) => t.name === ev.data.name && t.status === "running");
            const callArgs = lastRunning?.args ?? {};
            dispatch(toolFinished({ name: ev.data.name, result: ev.data.result }));
            if (applyToolResultToTrip(dispatch, ev.data.name, callArgs, ev.data.result)) {
              dispatch(notifyPanelUpdate());
            }
            break;
          }

          case "agent.message_segment": {
            const { segment_id, content, reasoning } = ev.data;
            // Attach any tools that ran since the previous segment to that
            // previous segment (they were called by the model invocation
            // that produced it).
            const liveTools = getState().agent.toolChain;
            if (liveTools.length > 0) {
              dispatch(attachToolsToLatestAssistant({ tools: liveTools }));
              dispatch(clearLiveTools());
            }
            // Push the new segment as its own assistant turn. Use the
            // planner's segment_id as the turn id so subsequent
            // agent.audio_chunk events can target the same turn for the
            // "speaking" highlight.
            if ((content && content.trim()) || (reasoning && reasoning.trim())) {
              dispatch(pushAssistantTurn({
                id: segment_id,
                content: content || "",
                reasoning: reasoning || "",
                toolChain: [],
                error: null,
              }));
            }
            break;
          }

          case "agent.audio_chunk": {
            // Only play audio when voice mode is on. The chunks are emitted
            // by the planner regardless (cheap to ignore on the client).
            if (getState().voice.mode !== "on") break;
            playAudioChunk(ev.data.segment_id, ev.data.data, {
              onSegmentStart: (id) => {
                dispatch(setSpokenMessageId(id));
                dispatch(setSpeaking(true));
              },
            });
            break;
          }

          case "agent.audio_done": {
            if (getState().voice.mode !== "on") break;
            finalizeAudioSegment(ev.data.segment_id, {
              onSegmentEnd: (_id) => {
                // No-op per-segment · we wait for onAllDone to clear speaking.
              },
              onAllDone: () => {
                dispatch(setSpeaking(false));
              },
            });
            break;
          }

          case "error":
            dispatch(setError(ev.data.message ?? "Unknown error"));
            dispatch(setLatestAssistantError(ev.data.message ?? "Unknown error"));
            break;

          case "agent.interrupt":
            // Phase 7 · LangGraph paused at capture_payment. Stamp the
            // pending approval state · the modal mounts on this.
            dispatch(setPendingApproval({
              kind: "payment_approval",
              authId: ev.data.auth_id,
              amountInr: ev.data.amount_inr,
              status: "pending",
            }));
            break;

          case "run.complete":
            break;
        }
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      dispatch(setError(msg));
      dispatch(setLatestAssistantError(msg));
    } finally {
      // Attach any remaining tools to the last segment, then clear live state.
      const liveTools = getState().agent.toolChain;
      if (liveTools.length > 0) {
        dispatch(attachToolsToLatestAssistant({ tools: liveTools }));
        dispatch(clearLiveTools());
      }
      // If the run paused at the HITL gate, keep phase=awaiting_approval ·
      // we WILL come back via resumeAgentRun/cancelAgentRun. Otherwise we're
      // done.
      const stillPaused = getState().agent.pendingApproval !== null;
      if (!stillPaused) {
        dispatch(setPhase("done"));
      }
      dispatch(resetForNewRun());
    }
  };
}


// ─── Phase 7 · HITL approval flow ──────────────────────────────────────
//
// runAgent (above) is the LLM-initiated turn. The user can also trigger
// runs via the HITL approval modal: clicking Approve calls resumeAgentRun,
// clicking Cancel calls cancelAgentRun. Both reuse the same per-event
// handler as runAgent · code duplication is intentional for clarity over
// the alternative (factoring a shared handler creates re-entrancy issues
// with the audio/segment counters).

export function resumeAgentRun() {
  return async (dispatch: AppDispatch, getState: () => RootState) => {
    const sessionId = getState().chat.sessionId;
    dispatch(setApprovalStatus("approving"));
    dispatch(setPhase("thinking"));
    try {
      await resumeAgent(sessionId, _handleStreamEvent(dispatch, getState));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      dispatch(setError(msg));
      dispatch(setLatestAssistantError(msg));
    } finally {
      const liveTools = getState().agent.toolChain;
      if (liveTools.length > 0) {
        dispatch(attachToolsToLatestAssistant({ tools: liveTools }));
        dispatch(clearLiveTools());
      }
      const stillPaused = getState().agent.pendingApproval?.status === "approving";
      if (stillPaused) {
        // Approve sequence completed without re-pausing → clear modal.
        dispatch(setPendingApproval(null));
      }
      dispatch(setPhase("done"));
      dispatch(resetForNewRun());
    }
  };
}

export function cancelAgentRun() {
  return async (dispatch: AppDispatch, getState: () => RootState) => {
    const sessionId = getState().chat.sessionId;
    dispatch(setApprovalStatus("cancelling"));
    dispatch(setPhase("thinking"));
    try {
      await cancelAgent(sessionId, _handleStreamEvent(dispatch, getState));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      dispatch(setError(msg));
      dispatch(setLatestAssistantError(msg));
    } finally {
      const liveTools = getState().agent.toolChain;
      if (liveTools.length > 0) {
        dispatch(attachToolsToLatestAssistant({ tools: liveTools }));
        dispatch(clearLiveTools());
      }
      // Cancel always clears the modal · the model's reaction message is
      // already in the chat by now.
      dispatch(setPendingApproval(null));
      dispatch(setPhase("done"));
      dispatch(resetForNewRun());
    }
  };
}


/** Factored per-event handler used by resumeAgentRun / cancelAgentRun.
 *  (runAgent has its own inline copy · keeping it explicit so the user
 *  message dispatch + reset ordering stays local to that path.) */
function _handleStreamEvent(
  dispatch: AppDispatch,
  getState: () => RootState,
): (ev: AgentEvent) => void {
  return (ev: AgentEvent) => {
    switch (ev.event) {
      case "state.phase":
        dispatch(setPhase(ev.data.phase));
        break;
      case "tool.started": {
        const args = ev.data.args ?? {};
        dispatch(toolStarted({ name: ev.data.name, args }));
        if (applyToolStartedToTrip(dispatch, ev.data.name, args)) {
          dispatch(notifyPanelUpdate());
        }
        break;
      }
      case "tool.finished": {
        const beforeChain = getState().agent.toolChain;
        const lastRunning = [...beforeChain]
          .reverse()
          .find((t) => t.name === ev.data.name && t.status === "running");
        const callArgs = lastRunning?.args ?? {};
        dispatch(toolFinished({ name: ev.data.name, result: ev.data.result }));
        if (applyToolResultToTrip(dispatch, ev.data.name, callArgs, ev.data.result)) {
          dispatch(notifyPanelUpdate());
        }
        break;
      }
      case "agent.message_segment": {
        const { segment_id, content, reasoning } = ev.data;
        const liveTools = getState().agent.toolChain;
        if (liveTools.length > 0) {
          dispatch(attachToolsToLatestAssistant({ tools: liveTools }));
          dispatch(clearLiveTools());
        }
        // Match runAgent's call shape · toolChain + error are required fields.
        if ((content && content.trim()) || (reasoning && reasoning.trim())) {
          dispatch(pushAssistantTurn({
            id: segment_id,
            content: content || "",
            reasoning: reasoning || "",
            toolChain: [],
            error: null,
          }));
        }
        break;
      }
      case "agent.audio_chunk": {
        if (getState().voice.mode !== "on") break;
        playAudioChunk(ev.data.segment_id, ev.data.data);
        dispatch(setSpeaking(true));
        dispatch(setSpokenMessageId(ev.data.segment_id));
        break;
      }
      case "agent.audio_done": {
        if (getState().voice.mode !== "on") break;
        finalizeAudioSegment(ev.data.segment_id, {
          onSegmentEnd: () => { /* per-segment noop */ },
          onAllDone: () => { dispatch(setSpeaking(false)); },
        });
        break;
      }
      case "error":
        dispatch(setError(ev.data.message ?? "Unknown error"));
        dispatch(setLatestAssistantError(ev.data.message ?? "Unknown error"));
        break;
      case "agent.interrupt":
        dispatch(setPendingApproval({
          kind: "payment_approval",
          authId: ev.data.auth_id,
          amountInr: ev.data.amount_inr,
          status: "pending",
        }));
        break;
      case "run.complete":
        break;
    }
  };
}
