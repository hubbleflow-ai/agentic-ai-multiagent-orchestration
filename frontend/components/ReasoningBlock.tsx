"use client";

/**
 * Streaming "Thinking" panel · ported from S5.
 *
 * Two callers:
 *   1. Live · from VoiceTranscript while a reasoning-mode turn is mid-flight.
 *      Reads from agentSlice.reasoning (grows as agent.reasoning_chunk arrives).
 *   2. Static · inside a finalised AssistantTurn that captured a non-empty
 *      reasoning snapshot. Same UI, just from props.
 *
 * Collapsed by default · click to expand. Streaming variant shows a pulsing
 * "Thinking" label and stays open while reasoning is in flight.
 */

import { useState } from "react";
import { ChevronRightIcon } from "./icons";
import { Markdown } from "./Markdown";

type Props = {
  reasoning: string;
  /** When true, show a pulsing "Thinking" label and force open. */
  streaming?: boolean;
  /** Initial open state if not streaming. */
  defaultOpen?: boolean;
};

export function ReasoningBlock({
  reasoning,
  streaming = false,
  defaultOpen = false,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  if (!reasoning) return null;

  const wordCount = reasoning.trim().split(/\s+/).length;

  return (
    <details
      open={open || streaming}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className={[
        "group rounded-xl border border-edge bg-surface/60 transition-colors hover:bg-surface",
        streaming ? "reasoning-streaming" : "",
      ].join(" ")}
    >
      <summary className="flex cursor-pointer items-center gap-2 px-3.5 py-2 text-[13px] text-muted">
        <ChevronRightIcon className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
        <span className="flex-1">
          {streaming ? (
            <>
              <span className="reasoning-pulse">Thinking</span>
              <span className="ml-1 text-muted/70">· {wordCount} words so far</span>
            </>
          ) : (
            <>
              Reasoning
              <span className="ml-1 text-muted/70">
                · {wordCount} {wordCount === 1 ? "word" : "words"}
              </span>
            </>
          )}
        </span>
      </summary>
      <div className="px-3.5 pb-3 pt-1" style={{ wordBreak: "break-word" }}>
        <Markdown variant="reasoning">{reasoning}</Markdown>
      </div>
    </details>
  );
}
