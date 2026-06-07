"use client";

/**
 * Phase 7 · HITL approval modal.
 *
 * Renders when agentSlice.pendingApproval is non-null, which happens
 * when the planner's LangGraph paused at the capture_payment node
 * (interrupt_before). User clicks Approve → resumeAgentRun (which calls
 * /agent/resume → graph executes the real capture). User clicks Cancel
 * → cancelAgentRun (which injects a synthetic decline + resumes so the
 * model wraps up the conversation gracefully).
 *
 * The modal is keyboard-friendly · Enter approves, Esc cancels.
 */

import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import { resumeAgentRun, cancelAgentRun } from "@/store/eventStream";
import { CheckIcon, XIcon } from "./icons";

function formatINR(amount: number | null): string {
  if (amount == null) return "—";
  // Format in Indian lakh notation for amounts ≥ 1L, else plain rupees.
  if (amount >= 100_000) {
    const lakhs = amount / 100_000;
    return `₹${lakhs.toFixed(2)} lakh`;
  }
  return `₹${amount.toLocaleString("en-IN")}`;
}

export function ApprovalModal() {
  const dispatch = useDispatch<AppDispatch>();
  const pending = useSelector((s: RootState) => s.agent.pendingApproval);

  // Keyboard shortcuts · Enter approve, Esc cancel.
  useEffect(() => {
    if (!pending || pending.status !== "pending") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        dispatch(resumeAgentRun());
      } else if (e.key === "Escape") {
        e.preventDefault();
        dispatch(cancelAgentRun());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, dispatch]);

  if (!pending) return null;

  const busy = pending.status !== "pending";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
    >
      <div className="w-full max-w-md rounded-2xl border border-edge bg-surface p-6 shadow-2xl">
        <div className="mb-4 flex items-baseline justify-between">
          <h2
            id="approval-modal-title"
            className="font-serif text-[20px] font-medium text-ink"
          >
            Confirm payment
          </h2>
          <span className="rounded-full bg-accentSoft px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-wider text-accent">
            HITL gate
          </span>
        </div>

        <p className="mb-2 text-[14px] text-muted">
          The agent has paused before charging your card. Approve to capture,
          or cancel to abort.
        </p>

        <div className="mb-5 rounded-xl border border-edge bg-bg px-4 py-3">
          <div className="text-[11.5px] uppercase tracking-wider text-muted">
            Amount
          </div>
          <div className="font-mono text-[24px] font-medium text-ink">
            {formatINR(pending.amountInr)}
          </div>
          {pending.authId && (
            <div className="mt-1 font-mono text-[11px] text-muted">
              auth · {pending.authId}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => dispatch(cancelAgentRun())}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full border border-edge bg-bg px-4 py-2 text-[13px] font-medium text-ink hover:border-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            <XIcon className="h-[12px] w-[12px]" />
            {pending.status === "cancelling" ? "Cancelling…" : "Cancel"}
          </button>
          <button
            type="button"
            onClick={() => dispatch(resumeAgentRun())}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <CheckIcon className="h-[12px] w-[12px]" />
            {pending.status === "approving" ? "Approving…" : "Approve"}
          </button>
        </div>

        <p className="mt-4 text-[11px] text-muted/80">
          Enter to approve · Esc to cancel
        </p>
      </div>
    </div>
  );
}
