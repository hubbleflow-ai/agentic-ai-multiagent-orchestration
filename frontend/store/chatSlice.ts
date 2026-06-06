import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { ToolCall } from "./agentSlice";

/**
 * Persistent turn-by-turn conversation history. Each user message and each
 * completed agent turn lives here. The currently-in-flight turn lives in
 * agentSlice and is snapshotted in on run.complete.
 */

export type UserTurn = {
  id: string;
  role: "user";
  content: string;
  ts: number;
};

export type AssistantTurn = {
  id: string;
  role: "assistant";
  content: string;
  reasoning: string;
  toolChain: ToolCall[];
  error: string | null;
  ts: number;
};

export type Turn = UserTurn | AssistantTurn;

type ChatSliceState = {
  sessionId: string;
  turns: Turn[];
};

function newSessionId(): string {
  return "sess-" + Math.random().toString(36).slice(2, 12);
}

const initialState: ChatSliceState = {
  sessionId: newSessionId(),
  turns: [],
};

const chatSlice = createSlice({
  name: "chat",
  initialState,
  reducers: {
    pushUserTurn(state, action: PayloadAction<{ content: string }>) {
      state.turns.push({
        id: `u-${Date.now()}`,
        role: "user",
        content: action.payload.content,
        ts: Date.now(),
      });
    },
    pushAssistantTurn(
      state,
      action: PayloadAction<{
        id?: string;            // optional · usually the planner's segment_id so
                                //   audio events can target this turn
        content: string;
        reasoning: string;
        toolChain: ToolCall[];
        error: string | null;
      }>,
    ) {
      state.turns.push({
        id: action.payload.id ?? `a-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: "assistant",
        content: action.payload.content,
        reasoning: action.payload.reasoning,
        toolChain: action.payload.toolChain,
        error: action.payload.error,
        ts: Date.now(),
      });
    },
    /** Attach a toolChain to the LATEST assistant turn. Used when tools
     *  finish executing between segments — they belong to the segment
     *  that called them (the previous assistant turn). */
    attachToolsToLatestAssistant(
      state,
      action: PayloadAction<{ tools: ToolCall[] }>,
    ) {
      for (let i = state.turns.length - 1; i >= 0; i--) {
        const t = state.turns[i];
        if (t.role === "assistant") {
          t.toolChain = [...t.toolChain, ...action.payload.tools];
          return;
        }
      }
    },
    /** Set/replace the LATEST assistant turn's error. */
    setLatestAssistantError(state, action: PayloadAction<string>) {
      for (let i = state.turns.length - 1; i >= 0; i--) {
        const t = state.turns[i];
        if (t.role === "assistant") {
          t.error = action.payload;
          return;
        }
      }
    },
    resetSession(state) {
      state.sessionId = newSessionId();
      state.turns = [];
    },
  },
});

export const {
  pushUserTurn,
  pushAssistantTurn,
  attachToolsToLatestAssistant,
  setLatestAssistantError,
  resetSession,
} = chatSlice.actions;

export const chatReducer = chatSlice.reducer;
