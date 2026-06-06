"use client";

/**
 * Persistent multi-turn transcript · chat-style.
 *
 * Each LLM invocation in an agent run becomes its OWN AssistantTurn —
 * a single user message often spawns multiple assistant turns interleaved
 * with tool execution. Each turn is spoken by VoicePlaybackController as
 * it arrives, so narrations like "Let me check flights now" are audible
 * mid-tool-execution instead of after the whole turn finishes.
 *
 * Live in-flight UI:
 *   - PhaseIndicator (Thinking / Calling tool / Responding) at the bottom
 *     while the agent is mid-run
 *   - Live toolChain shown as a collapsible "Actions" block (these tools
 *     get attached to the segment that called them when the NEXT segment
 *     arrives)
 *
 * Autoscroll on every meaningful change.
 */

import { useEffect, useRef, useState } from "react";
import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import type { AssistantTurn } from "@/store/chatSlice";
import type { ToolCall } from "@/store/agentSlice";
import { ChevronRightIcon } from "./icons";
import { ReasoningBlock } from "./ReasoningBlock";
import { VoiceSyncedMarkdown } from "./VoiceSyncedMarkdown";

export function VoiceTranscript() {
  const turns = useSelector((s: RootState) => s.chat.turns);
  const phase = useSelector((s: RootState) => s.agent.phase);
  const liveChain = useSelector((s: RootState) => s.agent.toolChain);
  const liveError = useSelector((s: RootState) => s.agent.error);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
    return () => cancelAnimationFrame(id);
  }, [turns.length, phase, liveChain.length]);

  const isStreaming =
    phase === "thinking" || phase === "tool_calling" || phase === "responding";
  const hasLiveActivity = isStreaming || liveChain.length > 0 || !!liveError;

  if (turns.length === 0 && !hasLiveActivity) return null;

  return (
    <div className="space-y-6 pb-4">
      {turns.map((t) =>
        t.role === "user" ? (
          <UserBubble key={t.id} content={t.content} />
        ) : (
          <AssistantTurnView key={t.id} turn={t} />
        ),
      )}

      {hasLiveActivity && (
        <LiveAssistantActivity
          chain={liveChain}
          error={liveError}
          phase={phase}
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-3xl rounded-tr-md bg-surface px-4 py-2.5 text-[15px] leading-relaxed text-ink shadow-soft">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}

function AssistantTurnView({ turn }: { turn: AssistantTurn }) {
  return (
    <div className="min-w-0 space-y-2.5 bg-transparent">
      {turn.reasoning && <ReasoningBlock reasoning={turn.reasoning} />}
      {turn.toolChain.length > 0 && <ToolChainCollapsible chain={turn.toolChain} />}
      {turn.content && (
        <VoiceSyncedMarkdown text={turn.content} done messageId={turn.id} />
      )}
      {turn.error && <ErrorBlock message={turn.error} />}
    </div>
  );
}

function LiveAssistantActivity({
  chain,
  error,
  phase,
}: {
  chain: ToolCall[];
  error: string | null;
  phase: string;
}) {
  const streaming =
    phase === "thinking" || phase === "tool_calling" || phase === "responding";
  return (
    <div className="min-w-0 space-y-2.5 bg-transparent">
      {streaming && <PhaseIndicator phase={phase} />}
      {chain.length > 0 && <ToolChainCollapsible chain={chain} live />}
      {error && <ErrorBlock message={error} />}
    </div>
  );
}

const PHASE_LABELS: Record<string, string> = {
  thinking: "Thinking",
  tool_calling: "Calling tool",
  responding: "Responding",
};

function PhaseIndicator({ phase }: { phase: string }) {
  const label = PHASE_LABELS[phase] ?? "Working";
  return (
    <div className="flex items-center gap-1.5 px-1 text-[13px] text-muted">
      <span className="thinking-dot inline-block h-1.5 w-1.5 rounded-full bg-accent" />
      <span className="thinking-dot thinking-dot-2 inline-block h-1.5 w-1.5 rounded-full bg-accent" />
      <span className="thinking-dot thinking-dot-3 inline-block h-1.5 w-1.5 rounded-full bg-accent" />
      <span className="ml-1.5">{label}…</span>
    </div>
  );
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2">
      <p className="text-[13px] text-danger">{message}</p>
    </div>
  );
}

function ToolChainCollapsible({ chain, live = false }: { chain: ToolCall[]; live?: boolean }) {
  const [open, setOpen] = useState(live);
  return (
    <div className="rounded-xl border border-edge bg-surface/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-[13px] text-muted transition-colors hover:text-ink"
      >
        <ChevronRightIcon
          className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span>
          Actions · {chain.length} {chain.length === 1 ? "step" : "steps"}
        </span>
      </button>
      {open && (
        <ol className="space-y-1.5 px-3.5 pb-3">
          {chain.map((t, idx) => (
            <li
              key={t.id}
              className="cot-card border-l-2 border-l-accent/30 pl-3 text-[13px]"
              style={{ animationDelay: `${idx * 30}ms` }}
            >
              <div className="flex items-center gap-2">
                <StatusDot status={t.status} />
                <span className="font-medium text-ink">{t.name}</span>
                <span className="flex-1 truncate text-muted">
                  ({summary(t.args, 80)})
                </span>
              </div>
              {t.status === "done" && t.result !== undefined && (
                <div className="mt-0.5 truncate pl-4 text-[12px] text-muted">
                  → {summary(t.result, 140)}
                </div>
              )}
              {t.status === "error" && (
                <div className="mt-0.5 pl-4 text-[12px] text-danger">failed</div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: ToolCall["status"] }) {
  if (status === "running")
    return <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />;
  if (status === "error")
    return <span className="inline-block h-1.5 w-1.5 rounded-full bg-danger" />;
  return <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" />;
}

function summary(value: unknown, limit = 80): string {
  if (value == null) return "";
  if (typeof value === "string") {
    return value.length > limit ? value.slice(0, limit) + "…" : value;
  }
  try {
    const s = JSON.stringify(value);
    return s.length > limit ? s.slice(0, limit) + "…" : s;
  } catch {
    return String(value);
  }
}
