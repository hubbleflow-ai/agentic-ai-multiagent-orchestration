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
import { streamAgent, type AgentEvent } from "@/lib/api";
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
    case "build_itinerary":
    case "revise_itinerary": {
      if (Array.isArray(r.days) && r.city) {
        dispatch(upsertItinerary({ city: r.city, days: r.days }));
        return true;
      }
      return false;
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
      dispatch(setPhase("done"));
      // Don't push a final empty turn — segments already pushed.
      dispatch(resetForNewRun());
    }
  };
}
