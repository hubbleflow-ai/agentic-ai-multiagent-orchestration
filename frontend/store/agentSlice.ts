import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

/**
 * Live in-flight state for the current agent turn.
 *
 * IMPORTANT: with the per-segment model, the agent's text response no
 * longer accumulates here — each LLM invocation becomes its own
 * AssistantTurn in chatSlice the moment it arrives. agentSlice only
 * tracks the LIVE accumulators that haven't been snapshotted yet:
 *   - phase     · current stage of the run
 *   - toolChain · tools called since the last segment was pushed
 *                  (these belong to the segment that JUST appeared above
 *                  and get attached to it when the next segment arrives,
 *                  or attached to the last segment on run.complete)
 *   - error     · any stream-level error
 */

export type AgentPhase =
  | "idle"
  | "thinking"
  | "tool_calling"
  | "responding"
  | "done"
  | "awaiting_approval";  // Phase 7 · paused at HITL capture_payment gate

/** Phase 7 · Pending HITL approval. Non-null → render the approval modal. */
export type PendingApproval = {
  kind: "payment_approval";
  authId: string | null;
  amountInr: number | null;
  status: "pending" | "approving" | "cancelling";
};

export type ToolCall = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
  result?: unknown;
  startedAt: string;
  finishedAt?: string;
};

type AgentSliceState = {
  phase: AgentPhase;
  toolChain: ToolCall[];
  error: string | null;
  pendingApproval: PendingApproval | null;
};

const initialState: AgentSliceState = {
  phase: "idle",
  toolChain: [],
  error: null,
  pendingApproval: null,
};

const agentSlice = createSlice({
  name: "agent",
  initialState,
  reducers: {
    setPhase(state, action: PayloadAction<AgentPhase>) {
      state.phase = action.payload;
    },

    toolStarted(
      state,
      action: PayloadAction<{ name: string; args: Record<string, unknown> }>,
    ) {
      const { name, args } = action.payload;
      state.toolChain.push({
        id: `${name}-${state.toolChain.length}-${Date.now()}`,
        name,
        args,
        status: "running",
        startedAt: new Date().toISOString(),
      });
    },

    toolFinished(
      state,
      action: PayloadAction<{ name: string; result: unknown }>,
    ) {
      const { name, result } = action.payload;
      for (let i = state.toolChain.length - 1; i >= 0; i--) {
        const t = state.toolChain[i];
        if (t.name === name && t.status === "running") {
          t.status = "done";
          t.result = result;
          t.finishedAt = new Date().toISOString();
          break;
        }
      }
    },

    /** Clear the live toolChain after it's been attached to a persisted
     *  segment turn. */
    clearLiveTools(state) {
      state.toolChain = [];
    },

    setError(state, action: PayloadAction<string | null>) {
      state.error = action.payload;
    },

    resetForNewRun(state) {
      state.phase = "idle";
      state.toolChain = [];
      state.error = null;
      // Don't clear pendingApproval here · resumes/cancels reuse the run
      // and we want the modal to STAY until the user clicks Approve or
      // Cancel. It clears via setPendingApproval(null) explicitly.
    },

    /** Phase 7 · stamp / clear the HITL pending-approval modal state. */
    setPendingApproval(state, action: PayloadAction<PendingApproval | null>) {
      state.pendingApproval = action.payload;
    },
    /** Phase 7 · convenience to flip status while resume/cancel in flight. */
    setApprovalStatus(state, action: PayloadAction<PendingApproval["status"]>) {
      if (state.pendingApproval) state.pendingApproval.status = action.payload;
    },
  },
});

export const {
  setPhase,
  toolStarted,
  toolFinished,
  clearLiveTools,
  setError,
  resetForNewRun,
  setPendingApproval,
  setApprovalStatus,
} = agentSlice.actions;

export const agentReducer = agentSlice.reducer;
